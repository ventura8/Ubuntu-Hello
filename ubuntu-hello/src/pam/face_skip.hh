#ifndef FACE_SKIP_H_
#define FACE_SKIP_H_

#include <string>

/** Default directory for per-user face-skip markers (file per username). */
inline constexpr const char *FACE_SKIP_DEFAULT_BASE = "/run/ubuntu-hello/face-skip";

/**
 * True for greeter/login PAM services (GDM/LightDM/SDDM/login/screensaver).
 * Does not match sudo, polkit, or other repeated interactive prompts.
 */
auto is_greeter_service(const std::string &service) -> bool;

/**
 * True when skip-after-face-failure applies for this PAM service.
 *
 * Only legacy *screensaver* PAM names. GNOME lock uses gdm-password (same as
 * login); applying skip there blocks Esc→Enter face retries, so it is
 * intentionally excluded — each unlock attempt may run face again.
 */
auto face_skip_applies(const std::string &service) -> bool;

/**
 * Create a skip marker for username under base.
 * Rejects unsafe usernames. Directory mode 0700, file mode 0600.
 * @return true on success
 */
auto face_skip_set(const std::string &username,
                   const std::string &base = FACE_SKIP_DEFAULT_BASE) -> bool;

/**
 * Remove skip marker for username if present.
 * @return true if cleared or already absent; false on unsafe username / I/O error
 */
auto face_skip_clear(const std::string &username,
                     const std::string &base = FACE_SKIP_DEFAULT_BASE) -> bool;

/**
 * True if a skip marker exists for username.
 */
auto face_skip_active(const std::string &username,
                      const std::string &base = FACE_SKIP_DEFAULT_BASE) -> bool;

#endif // FACE_SKIP_H_
