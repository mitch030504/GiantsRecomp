// Raw 48 kHz capture probe: record exactly what the game submits to SDL's audio stream,
// before SDL converts it to the device rate. Build as a shared object and preload it with
// PROBE_SO=<path>; GIANTS_AUDIO_PROBE selects the output file.
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>

namespace {
using PutFn = bool (*)(void*, const void*, int);
PutFn real_put = nullptr;
FILE* sink = nullptr;
pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
}  // namespace

extern "C" bool SDL_PutAudioStreamData(void* stream, const void* buffer, int length) {
  if (!real_put) {
    real_put = reinterpret_cast<PutFn>(dlsym(RTLD_NEXT, "SDL_PutAudioStreamData"));
  }
  pthread_mutex_lock(&lock);
  if (!sink) {
    const char* path = getenv("GIANTS_AUDIO_PROBE");
    if (path && *path) {
      sink = fopen(path, "wb");
      if (sink) setvbuf(sink, nullptr, _IONBF, 0);
    }
  }
  if (sink && buffer && length > 0) {
    fwrite(buffer, 1, static_cast<size_t>(length), sink);
  }
  pthread_mutex_unlock(&lock);
  return real_put ? real_put(stream, buffer, length) : false;
}
