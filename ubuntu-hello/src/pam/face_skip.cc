#include "face_skip.hh"

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <string>

namespace {

auto is_safe_username(const std::string &user) -> bool {
  if (user.empty()) {
    return false;
  }
  for (size_t i = 0; i < user.size(); ++i) {
    char chr = user[i];
    if ((chr < 'a' || chr > 'z') && (chr < 'A' || chr > 'Z') &&
        (chr < '0' || chr > '9') && chr != '_' && chr != '-' && chr != '.' &&
        (chr != '$' || i + 1 != user.size())) {
      return false;
    }
  }
  return true;
}

auto starts_with(const std::string &value, const char *prefix) -> bool {
  const size_t len = std::strlen(prefix);
  return value.size() >= len && value.compare(0, len, prefix) == 0;
}

auto ensure_dir(const std::string &path, mode_t mode) -> bool {
  struct stat path_stat {};
  if (stat(path.c_str(), &path_stat) == 0) {
    return S_ISDIR(path_stat.st_mode);
  }
  if (mkdir(path.c_str(), mode) != 0 && errno != EEXIST) {
    return false;
  }
  chmod(path.c_str(), mode);
  return true;
}

/**
 * Create parent directories for base (e.g. /run/ubuntu-hello for
 * /run/ubuntu-hello/face-skip), then base itself.
 */
auto ensure_skip_base(const std::string &base) -> bool {
  if (base.empty() || base[0] != '/') {
    return false;
  }

  std::string partial;
  for (size_t i = 1; i < base.size(); ++i) {
    if (base[i] == '/') {
      partial = base.substr(0, i);
      if (partial != "/" && !ensure_dir(partial, 0700)) {
        return false;
      }
    }
  }
  return ensure_dir(base, 0700);
}

auto marker_path(const std::string &username, const std::string &base)
    -> std::string {
  return base + "/" + username;
}

} // namespace

auto is_greeter_service(const std::string &service) -> bool {
  if (service.empty()) {
    return false;
  }
  if (service == "gdm-password" || service == "login") {
    return true;
  }
  if (starts_with(service, "lightdm") || starts_with(service, "sddm")) {
    return true;
  }
  if (service.find("screensaver") != std::string::npos) {
    return true;
  }
  return false;
}

auto face_skip_applies(const std::string &service) -> bool {
  // Only explicit screensaver PAM services. Do not use gdm-password + session
  // detection: GNOME lock and login share gdm-password, and skip-after-failure
  // there prevents Esc→Enter face retries on the lock screen.
  return !service.empty() &&
         service.find("screensaver") != std::string::npos;
}

auto face_skip_set(const std::string &username, const std::string &base)
    -> bool {
  if (!is_safe_username(username)) {
    return false;
  }
  if (!ensure_skip_base(base)) {
    return false;
  }

  const std::string path = marker_path(username, base);
  int marker_fd =
      open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
  if (marker_fd < 0) {
    return false;
  }
  fchmod(marker_fd, 0600);
  close(marker_fd);
  return true;
}

auto face_skip_clear(const std::string &username, const std::string &base)
    -> bool {
  if (!is_safe_username(username)) {
    return false;
  }
  const std::string path = marker_path(username, base);
  return unlink(path.c_str()) == 0 || errno == ENOENT;
}

auto face_skip_active(const std::string &username, const std::string &base)
    -> bool {
  if (!is_safe_username(username)) {
    return false;
  }
  const std::string path = marker_path(username, base);
  struct stat path_stat {};
  return stat(path.c_str(), &path_stat) == 0 && S_ISREG(path_stat.st_mode);
}
