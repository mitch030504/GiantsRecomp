#!/usr/bin/env python3
"""Repair register-save entry points in the pinned Giants XEX's Ghidra project.

Usage: python ghidra_fix_ppc_helpers.py <project_dir> <project_name>
Requires PyGhidra and GHIDRA_INSTALL_DIR. Close other users of the project first.
"""

from pathlib import Path
import sys

import pyghidra


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    pyghidra.start()
    from ghidra.app.cmd.function import CreateFunctionCmd
    from ghidra.app.decompiler import DecompInterface
    from ghidra.app.plugin.core.analysis import AutoAnalysisManager
    from ghidra.program.model.address import AddressSet
    from ghidra.program.model.listing import FlowOverride, Program
    from ghidra.program.model.pcode import PcodeOp
    from ghidra.program.model.symbol import SourceType
    from ghidra.util.task import TaskMonitor

    with pyghidra.open_project(str(Path(sys.argv[1]).resolve()), sys.argv[2]) as project:
        with pyghidra.program_context(project, "/default.xex") as program:
            if program.getExecutableSHA256() != (
                "12d1e29e4f07fbc559bc1a4988dd6bf346c3d16e30cbf862e2683e862f9fc09e"
            ):
                raise ValueError("This repair supports only the project's pinned default.xex")
            addresses = program.getAddressFactory()
            memory = program.getMemory()
            functions = program.getFunctionManager()
            listing = program.getListing()

            def addr(value):
                return addresses.getAddress(hex(value))

            # Validate the actual instructions before changing any analysis metadata.
            for reg in range(14, 32):
                word = 0xF8010000 | (reg << 21) | ((-8 * (33 - reg)) & 0xFFFF)
                assert memory.getInt(addr(0x825DF9A0 + 4 * (reg - 14))) & 0xFFFFFFFF == word
            assert memory.getInt(addr(0x825DF9E8)) & 0xFFFFFFFF == 0x9181FFF8
            assert memory.getInt(addr(0x825DF9EC)) & 0xFFFFFFFF == 0x4E800020

            callers = set()
            for offset in range(0, 72, 4):
                for ref in program.getReferenceManager().getReferencesTo(addr(0x825DF9A0 + offset)):
                    caller = functions.getFunctionContaining(ref.getFromAddress())
                    if ref.getReferenceType().isCall() and caller:
                        callers.add(caller.getEntryPoint())

            transaction = program.startTransaction("Codex: repair Xbox register-save suffixes")
            commit = False
            try:
                for offset in range(0, 80, 4):
                    functions.removeFunction(addr(0x825DF9A0 + offset))
                for reg in range(14, 32):
                    start = 0x825DF9A0 + 4 * (reg - 14)
                    end = start + 3 if reg < 31 else 0x825DF9EF
                    # Each suffix falls through to the next; inline preserves argument registers.
                    function = functions.createFunction(
                        f"__savegprlr_{reg}", addr(start), AddressSet(addr(start), addr(end)),
                        SourceType.USER_DEFINED,
                    )
                    function.setInline(True)
                    function.setNoReturn(False)
                for entry in callers:
                    function = functions.getFunctionAt(entry)
                    for instruction in listing.getInstructions(function.getBody(), True):
                        if any(0x825DF9A0 <= dest.getOffset() < 0x825DF9E8
                               for dest in instruction.getFlows()):
                            instruction.setFlowOverride(FlowOverride.NONE)
                    CreateFunctionCmd.fixupFunctionBody(program, function, TaskMonitor.DUMMY)
                AutoAnalysisManager.getAnalysisManager(program).initializeOptions()
                # This analyzer mistakes the overlapping ABI helper suffixes for non-returning calls.
                program.getOptions(Program.ANALYSIS_PROPERTIES).setBoolean(
                    "Non-Returning Functions - Discovered", False,
                )
                pyghidra.analyze(program)
                decompiler = DecompInterface()
                decompiler.openProgram(program)
                try:
                    result = decompiler.decompileFunction(
                        functions.getFunctionAt(addr(0x82190EC0)), 60, TaskMonitor.DUMMY,
                    )
                    assert result.decompileCompleted(), result.getErrorMessage()
                    high = result.getHighFunction()
                    assert high.getFunctionPrototype().getNumParams() >= 1
                    calls = [op.getInput(0).getAddress().getOffset()
                             for op in high.getPcodeOps() if op.getOpcode() == PcodeOp.CALL]
                    assert 0x821910A8 in calls, "UI body was truncated"
                    assert not any(0x825DF9A0 <= dest < 0x825DF9E8 for dest in calls)
                finally:
                    decompiler.dispose()
                commit = True
            finally:
                program.endTransaction(transaction, commit)
            program.save("Codex: verified PPC helper repair and analysis", TaskMonitor.DUMMY)
            print(f"Repaired 18 save helpers; refreshed {len(callers)} callers; analysis saved.")


if __name__ == "__main__":
    main()
