#include <nanobind/nanobind.h>

extern void bind_msquic(nanobind::module_& m);

NB_MODULE(msquic_ext, m) {
  bind_msquic(m);
}
