#include "face_skip.hh"

#include <iostream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace {

auto fail(const char *msg) -> int {
  std::cerr << "FAIL: " << msg << "\n";
  return 1;
}

} // namespace

auto main(int argc, char **argv) -> int {
  if (argc < 2) {
    return fail("usage: pam_face_skip_test <tmpdir>");
  }
  const std::string base = std::string(argv[1]) + "/face-skip";

  if (is_greeter_service("sudo") || is_greeter_service("polkit-1")) {
    return fail("sudo/polkit must not be greeter services");
  }
  if (!is_greeter_service("gdm-password") || !is_greeter_service("login") ||
      !is_greeter_service("lightdm") || !is_greeter_service("lightdm-greeter") ||
      !is_greeter_service("sddm") || !is_greeter_service("sddm-greeter") ||
      !is_greeter_service("gnome-screensaver")) {
    return fail("expected greeter PAM services not detected");
  }

  // GNOME lock shares gdm-password with login — must NOT skip-after-failure so
  // Esc→Enter can retry face. Only legacy *screensaver* PAM names skip.
  if (face_skip_applies("gdm-password") || face_skip_applies("login") ||
      face_skip_applies("lightdm") || face_skip_applies("lightdm-greeter") ||
      face_skip_applies("sddm") || face_skip_applies("sddm-greeter") ||
      face_skip_applies("sudo") || face_skip_applies("")) {
    return fail("gdm-password/login/sudo must not use face-skip-after-failure");
  }
  if (!face_skip_applies("gnome-screensaver") ||
      !face_skip_applies("gdm-screensaver") ||
      !face_skip_applies("lightdm-screensaver") ||
      !face_skip_applies("kscreenlocker-screensaver")) {
    return fail("expected screensaver PAM services for face-skip");
  }

  if (face_skip_set("../evil", base) || face_skip_active("../evil", base) ||
      face_skip_clear("../evil", base)) {
    return fail("unsafe username must be rejected");
  }
  if (face_skip_set("bad/user", base) || face_skip_set("", base)) {
    return fail("path-like or empty username must be rejected");
  }

  if (face_skip_active("alice", base)) {
    return fail("skip should be inactive before set");
  }
  if (!face_skip_set("alice", base)) {
    return fail("face_skip_set(alice) failed");
  }
  if (!face_skip_active("alice", base)) {
    return fail("skip should be active after set");
  }

  struct stat st {};
  if (stat(base.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) {
    return fail("skip base directory missing");
  }
  if ((st.st_mode & 0777) != 0700) {
    return fail("skip base directory mode is not 0700");
  }
  const std::string marker = base + "/alice";
  if (stat(marker.c_str(), &st) != 0 || !S_ISREG(st.st_mode)) {
    return fail("skip marker file missing");
  }
  if ((st.st_mode & 0777) != 0600) {
    return fail("skip marker file mode is not 0600");
  }

  if (face_skip_active("bob", base)) {
    return fail("other user must not inherit skip");
  }
  if (!face_skip_clear("alice", base)) {
    return fail("face_skip_clear(alice) failed");
  }
  if (face_skip_active("alice", base)) {
    return fail("skip should be inactive after clear");
  }
  if (!face_skip_clear("alice", base)) {
    return fail("clear of missing marker should succeed");
  }

  std::cout << "pam_face_skip_test: OK\n";
  return 0;
}
