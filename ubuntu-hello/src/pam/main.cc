#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <cstdio>
#include <fcntl.h>

#include <glob.h>
#include <libintl.h>
#include <pthread.h>
#include <spawn.h>
#include <stdexcept>
#include <sys/signalfd.h>
#include <sys/stat.h>
#include <sys/syslog.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <syslog.h>
#include <unistd.h>

#include <array>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <fstream>
#include <functional>
#include <future>
#include <mutex>
#include <string>
#include <tuple>

#include <INIReader.h>

#include <security/pam_appl.h>
#include <security/pam_ext.h>
#include <security/pam_modules.h>

#include "aes_gcm_uh1.hh"
#include "enter_device.hh"
#include "face_skip.hh"
#include "main.hh"
#include "optional_task.hh"
#include <paths.hh>

const auto CHILD_TERM_TIMEOUT =
    std::chrono::duration<int, std::chrono::milliseconds::period>(500);

const auto DEFAULT_TIMEOUT =
    std::chrono::duration<int, std::chrono::milliseconds::period>(100);
const auto MAX_RETRIES = 5;

#define S(msg) gettext(msg)

/**
 * Helper to check if a PAM service is in the comma-separated ignore list
 * @param  service_list  Comma-separated list of ignored services
 * @param  service       The current PAM service name
 * @return               True if the service is in the list
 */
auto is_service_ignored(const std::string &service_list, const std::string &service) -> bool {
  std::string list = service_list;
  size_t pos = 0;
  while ((pos = list.find(',')) != std::string::npos) {
    std::string token = list.substr(0, pos);
    // Trim whitespace
    size_t first = token.find_first_not_of(" \t\r\n");
    if (first != std::string::npos) {
      size_t last = token.find_last_not_of(" \t\r\n");
      token = token.substr(first, (last - first + 1));
    } else {
      token = "";
    }
    if (token == service) {
      return true;
    }
    list.erase(0, pos + 1);
  }
  // Trim remaining part
  size_t first = list.find_first_not_of(" \t\r\n");
  if (first != std::string::npos) {
    size_t last = list.find_last_not_of(" \t\r\n");
    list = list.substr(first, (last - first + 1));
  } else {
    list = "";
  }
  return list == service;
}

/**
 * Inspect the status code returned by the compare process
 * @param  status        The status code
 * @param  conv_function The PAM conversation function
 * @return               A PAM return code
 */
auto ubuntu_hello_error(int status,
                 const std::function<int(int, const char *)> &conv_function)
    -> int {
  // If the process has exited
  if (WIFEXITED(status)) {
    // Get the status code returned
    status = WEXITSTATUS(status);

    switch (status) {
    case CompareError::NO_FACE_MODEL:
      syslog(LOG_NOTICE, "Failure, no face model known");
      break;
    case CompareError::TIMEOUT_REACHED:
      conv_function(PAM_ERROR_MSG, S("Failure, timeout reached"));
      syslog(LOG_ERR, "Failure, timeout reached");
      break;
    case CompareError::ABORT:
      syslog(LOG_ERR, "Failure, general abort");
      break;
    case CompareError::TOO_DARK:
      conv_function(PAM_ERROR_MSG, S("Face detection image too dark"));
      syslog(LOG_ERR, "Failure, image too dark");
      break;
    case CompareError::INVALID_DEVICE:
      syslog(LOG_ERR,
             "Failure, not possible to open camera at configured path");
      break;
    default:
      conv_function(PAM_ERROR_MSG,
                    std::string(S("Unknown error: ") + status).c_str());
      syslog(LOG_ERR, "Failure, unknown error %d", status);
    }
  } else if (WIFSIGNALED(status)) {
    // We get the signal
    status = WTERMSIG(status);

    syslog(LOG_ERR, "Child killed by signal %s (%d)", strsignal(status),
           status);
  }

  // As this function is only called for error status codes, signal an error to
  // PAM
  return PAM_AUTH_ERR;
}

/**
 * Format the success message if the status is successful or log the error in
 * the other case
 * @param  username      Username
 * @param  status        Status code
 * @param  config        INI  configuration
 * @param  conv_function PAM conversation function
 * @return          Returns the conversation function return code
 */
auto ubuntu_hello_status(char *username, int status, const INIReader &config,
                  const std::function<int(int, const char *)> &conv_function)
    -> int {
  if (status != EXIT_SUCCESS) {
    return ubuntu_hello_error(status, conv_function);
  }

  if (!config.GetBoolean("core", "no_confirmation", true)) {
    // Printf-style %s so translators can reorder (gettext best practice).
    std::array<char, 512> identify_buf{};
    std::snprintf(identify_buf.data(), identify_buf.size(),
                  S("Identified face as %s"), username);
    conv_function(PAM_TEXT_INFO, identify_buf.data());
  }

  syslog(LOG_INFO, "Login approved");

  return PAM_SUCCESS;
}

/**
 * Try to set PAM_AUTHTOK from a stored keyring key file after successful
 * face authentication, so that downstream PAM modules can unlock the
 * session wallet automatically. Typical consumers of the same token:
 *   - pam_gnome_keyring (GNOME / Ubuntu-family DEs)
 *   - pam_kwallet5 (KDE Plasma / KWallet)
 * No separate sealed-blob format is used for KWallet; both unlock paths
 * reuse this PAM_AUTHTOK after face auth succeeds.
 * @param pamh      PAM handle
 * @param username  Authenticated username
 */
auto popen_as_root(const std::string &cmd, const char *type) -> FILE * {
  uid_t ruid = getuid();
  uid_t euid = geteuid();
  bool altered = false;

  if (euid == 0 && ruid != 0) {
    if (setreuid(0, 0) == 0) {
      altered = true;
    } else {
      syslog(LOG_ERR, "Failed to setreuid(0, 0): %s (%d)", strerror(errno), errno);
    }
  }

  FILE *file_pipe = popen(cmd.c_str(), type);

  if (altered) {
    if (setreuid(ruid, euid) != 0) {
      syslog(LOG_ERR, "Failed to restore UIDs to %d/%d: %s (%d)", ruid, euid, strerror(errno), errno);
      if (file_pipe != nullptr) {
        pclose(file_pipe);
      }
      return nullptr;
    }
  }

  return file_pipe;
}

void try_set_keyring_authtok(pam_handle_t *pamh, const char *username) {
  std::string tpm_pub = "/etc/ubuntu-hello/tpm-keys/" + std::string(username) + ".pub";
  std::string tpm_priv = "/etc/ubuntu-hello/tpm-keys/" + std::string(username) + ".priv";
  
  std::string password;
  struct stat pub_stat{};
  struct stat priv_stat{};
  
  if (stat(tpm_pub.c_str(), &pub_stat) == 0 && stat(tpm_priv.c_str(), &priv_stat) == 0) {
    // TPM keys exist, unseal password from TPM
    pid_t pid = getpid();
    std::string p_ctx = "/etc/ubuntu-hello/tpm-keys/p_" + std::to_string(pid) + ".ctx";
    std::string s_ctx = "/etc/ubuntu-hello/tpm-keys/s_" + std::to_string(pid) + ".ctx";
    
    std::string cmd = "tpm2_createprimary -C o -c " + p_ctx + " 2>/dev/null && "
                      "tpm2_load -C " + p_ctx + " -u " + tpm_pub + " -r " + tpm_priv + " -c " + s_ctx + " 2>/dev/null && "
                      "tpm2_unseal -c " + s_ctx + " 2>/dev/null; "
                      "rm -f " + p_ctx + " " + s_ctx + " 2>/dev/null";
                      
    FILE *file_pipe = popen_as_root(cmd, "r");
    if (file_pipe != nullptr) {
      std::array<char, 256> buf{};
      // Prefer fread over fgets so clang-analyzer does not flag a blocking
      // fgets while identify()'s mutex is still (falsely) considered held.
      const size_t got = fread(buf.data(), 1, buf.size() - 1, file_pipe);
      if (got > 0) {
        buf[got] = '\0';
        password = buf.data();
        const size_t newline_at = password.find('\n');
        if (newline_at != std::string::npos) {
          password.erase(newline_at);
        }
      }
      pclose(file_pipe);
    }
    
    if (password.empty()) {
      syslog(LOG_ERR, "Failed to unseal keyring password from TPM");
      return;
    }
  } else {
    // Software fallback: AES-256-GCM (UH1:) with root-only master key
    std::string key_file = "/etc/ubuntu-hello/keyring-keys/" + std::string(username);
    std::ifstream ifs(key_file);
    if (!ifs.is_open()) {
      return;
    }

    std::string ciphertext;
    std::getline(ifs, ciphertext);
    if (ciphertext.empty()) {
      return;
    }

    password = aes_gcm_decrypt_uh1(ciphertext);
    if (password.empty()) {
      return;
    }
  }

  int pam_err = pam_set_item(pamh, PAM_AUTHTOK, password.c_str());
  if (pam_err == PAM_SUCCESS) {
    syslog(LOG_INFO, "PAM_AUTHTOK set successfully for keyring unlocking");
  } else {
    syslog(LOG_ERR, "Failed to set PAM_AUTHTOK: %s", pam_strerror(pamh, pam_err));
  }
}

/**
 * Check if Ubuntu Hello should be enabled according to the configuration and the
 * environment.
 * @param  config INI configuration
 * @param  username Username
 * @return        Returns PAM_AUTHINFO_UNAVAIL if it shouldn't be enabled,
 * PAM_SUCCESS otherwise
 */
auto check_enabled(const INIReader &config, const char *username) -> int {
  // Stop executing if Ubuntu Hello has been disabled in the config
  if (config.GetBoolean("core", "disabled", false)) {
    syslog(LOG_INFO, "Skipped authentication, Ubuntu Hello is disabled");
    return PAM_AUTHINFO_UNAVAIL;
  }

  // Stop if we're in a remote shell and configured to exit
  if (config.GetBoolean("core", "abort_if_ssh", true)) {
    if (checkenv("SSH_CONNECTION") || checkenv("SSH_CLIENT") ||
        checkenv("SSH_TTY") || checkenv("SSHD_OPTS")) {
      syslog(LOG_INFO, "Skipped authentication, SSH session detected");
      return PAM_AUTHINFO_UNAVAIL;
    }
  }

  // Try to detect the laptop lid state and stop if it's closed
  if (config.GetBoolean("core", "abort_if_lid_closed", true)) {
    glob_t glob_result;

    // Get any files containing lid state
    int return_value =
        glob("/proc/acpi/button/lid/*/state", 0, nullptr, &glob_result);

    if (return_value != 0) {
      syslog(LOG_ERR, "Failed to read files from glob: %d", return_value);
      if (errno != 0) {
        syslog(LOG_ERR, "Underlying error: %s (%d)", strerror(errno), errno);
      }
    } else {
      for (size_t i = 0; i < glob_result.gl_pathc; i++) {
        std::ifstream file(std::string(glob_result.gl_pathv[i]));
        std::string lid_state;
        std::getline(file, lid_state, static_cast<char>(file.eof()));

        if (lid_state.find("closed") != std::string::npos) {
          globfree(&glob_result);

          syslog(LOG_INFO, "Skipped authentication, closed lid detected");
          return PAM_AUTHINFO_UNAVAIL;
        }
      }
    }
    globfree(&glob_result);
  }

  // pre-check if this user has face model file
  auto model_path = std::string(USER_MODELS_DIR) + "/" + username + ".dat";
  struct stat stat_;
  if (stat(model_path.c_str(), &stat_) != 0) {
    return PAM_AUTHINFO_UNAVAIL;
  }

  return PAM_SUCCESS;
}

/**
 * Terminate the compare process group (SIGTERM, then SIGKILL after grace).
 */
auto terminate_compare_group(pid_t child_pid, optional_task<int> &child_task)
    -> void {
  if (child_pid <= 0) {
    return;
  }
  kill(-child_pid, SIGTERM);
  if (child_task.wait(CHILD_TERM_TIMEOUT) == std::future_status::timeout) {
    syslog(LOG_WARNING,
           "Compare process group %d did not exit after SIGTERM, sending SIGKILL",
           child_pid);
    kill(-child_pid, SIGKILL);
  }
  child_task.stop(false);
}

/**
 * Update face-skip marker from compare exit status (screensaver PAM only).
 * Do not set skip on SIGTERM/cancel — Esc must allow a later face retry.
 */
auto update_face_skip_from_status(const char *username, int compare_status,
                                  bool skip_face_after_failure,
                                  bool face_skip_service) -> void {
  if (!WIFEXITED(compare_status)) {
    return;
  }
  if (WEXITSTATUS(compare_status) == EXIT_SUCCESS) {
    face_skip_clear(username);
    return;
  }
  if (skip_face_after_failure && face_skip_service &&
      face_skip_set(username)) {
    syslog(LOG_INFO, "Set face-skip marker after failed attempt for user %s",
           username);
  }
}

/**
 * Dismiss a concurrent password prompt after face auth finished (workaround).
 */
auto dismiss_authtok_prompt(
    Workaround workaround, optional_task<std::tuple<int, char *>> &pass_task,
    const std::function<int(int, const char *)> &conv_function) -> void {
  // UNSAFE: We cancel the thread using pthread, pam_get_authtok seems to be
  // a cancellation point
  if (workaround == Workaround::Native) {
    pass_task.stop(true);
    return;
  }
  if (workaround != Workaround::Input) {
    return;
  }

  // We check if we have the right permissions on /dev/uinput
  if (euidaccess("/dev/uinput", W_OK | R_OK) != 0) {
    syslog(LOG_WARNING, "Insufficient permissions to create the fake device");
    conv_function(PAM_ERROR_MSG,
                  S("Insufficient permissions to send Enter "
                    "press, waiting for user to press it instead"));
  } else {
    try {
      EnterDevice enter_device;
      int retries = 0;

      // We try to send it
      enter_device.send_enter_press();

      for (; retries < MAX_RETRIES &&
             pass_task.wait(DEFAULT_TIMEOUT) == std::future_status::timeout;
           retries++) {
        enter_device.send_enter_press();
      }

      if (retries == MAX_RETRIES) {
        syslog(LOG_WARNING,
               "Failed to send enter input before the retries limit");
        conv_function(PAM_ERROR_MSG, S("Failed to send Enter press, waiting "
                                       "for user to press it instead"));
      }
    } catch (std::runtime_error &err) {
      syslog(LOG_WARNING, "Failed to send enter input: %s", err.what());
      conv_function(PAM_ERROR_MSG, S("Failed to send Enter press, waiting "
                                     "for user to press it instead"));
    }
  }

  // We stop the thread (will block until the enter key is pressed if the
  // input wasn't focused or if the uinput device failed to send keypress)
  pass_task.stop(false);
}

/**
 * The main function, runs the identification and authentication
 * @param  pamh     The handle to interface directly with PAM
 * @param  flags    Flags passed on to us by PAM, XORed
 * @param  argc     Amount of rules in the PAM config (disregarded)
 * @param  argv     Options defined in the PAM config
 * @param  ask_auth_tok True if we should ask for a password too
 * @return          Returns a PAM return code
 */
auto identify(pam_handle_t *pamh, int flags, int argc, const char **argv,
              bool ask_auth_tok) -> int {
  INIReader config(CONFIG_FILE_PATH);
  openlog("pam_ubuntu_hello", 0, LOG_AUTHPRIV);

  // Error out if we could not read the config file
  if (config.ParseError() != 0) {
    syslog(LOG_ERR, "Failed to parse the configuration file: %d",
           config.ParseError());
    return PAM_SYSTEM_ERR;
  }

  // Will contain the responses from PAM functions
  int pam_res = PAM_IGNORE;

  // Check if current service should be ignored
  const char *service = nullptr;
  if (pam_get_item(pamh, PAM_SERVICE, reinterpret_cast<const void **>(&service)) == PAM_SUCCESS && service != nullptr) {
    std::string ignore_services = config.GetString("core", "ignore_services", "");
    if (is_service_ignored(ignore_services, service)) {
      syslog(LOG_INFO, "Skipped authentication, PAM service '%s' is ignored", service);
      return PAM_AUTHINFO_UNAVAIL;
    }
  }

  // Get the username from PAM, needed to match correct face model
  char *username = nullptr;
  pam_res = pam_get_user(pamh, const_cast<const char **>(&username), nullptr);
  if (pam_res != PAM_SUCCESS || username == nullptr) {
    syslog(LOG_ERR, "Failed to get username");
    return pam_res == PAM_SUCCESS ? PAM_USER_UNKNOWN : pam_res;
  }

  // Validate username format
  if (!is_safe_username(username)) {
    syslog(LOG_ERR, "Invalid username format: %s", username);
    return PAM_AUTH_ERR;
  }

  // Check if we should continue
  pam_res = check_enabled(config, username);
  if (pam_res != PAM_SUCCESS) {
    return pam_res;
  }

  // Skip-after-failure only for legacy *screensaver* PAM services. GNOME lock
  // uses gdm-password (same as login); skipping there blocks Esc→Enter retries.
  const bool skip_face_after_failure =
      config.GetBoolean("core", "skip_face_after_failure", true);
  const bool face_skip_service =
      service != nullptr && face_skip_applies(service);
  if (skip_face_after_failure && face_skip_service &&
      face_skip_active(username)) {
    syslog(LOG_INFO,
           "Skipped face authentication, skip-after-failure active for user %s "
           "(service %s)",
           username, service);
    return PAM_AUTHINFO_UNAVAIL;
  }

  Workaround workaround =
      get_workaround(config.GetString("core", "workaround", "input"));

  // Will contain PAM conversation structure
  struct pam_conv *conv = nullptr;
  const void **conv_ptr =
      const_cast<const void **>(reinterpret_cast<void **>(&conv));

  // Retrieve the PAM conversation structure
  pam_res = pam_get_item(pamh, PAM_CONV, conv_ptr);
  if (pam_res != PAM_SUCCESS) {
    syslog(LOG_ERR, "Failed to acquire conversation");
    return pam_res;
  }

  // Wrap the PAM conversation function in our own, easier function
  auto conv_function = [conv](int msg_type, const char *msg_str) -> int {
    const struct pam_message msg = {.msg_style = msg_type, .msg = msg_str};
    const struct pam_message *msgp = &msg;

    struct pam_response res = {};
    struct pam_response *resp = &res;

    return conv->conv(1, &msgp, &resp, conv->appdata_ptr);
  };

  // Initialize gettext (system locale; UTF-8 codeset for PAM notices)
  setlocale(LC_ALL, "");
  bindtextdomain(GETTEXT_PACKAGE, LOCALEDIR);
  bind_textdomain_codeset(GETTEXT_PACKAGE, "UTF-8");
  textdomain(GETTEXT_PACKAGE);

  if (config.GetBoolean("core", "detection_notice", true)) {
    if ((conv_function(PAM_TEXT_INFO, S("Attempting facial authentication"))) !=
        PAM_SUCCESS) {
      syslog(LOG_ERR, "Failed to send detection notice");
    }
  }

  std::array<char *, 4> args = {const_cast<char *>(PYTHON_EXECUTABLE_PATH),
                                const_cast<char *>(COMPARE_PROCESS_PATH),
                                username, nullptr};
  pid_t child_pid = -1;

  posix_spawn_file_actions_t actions;
  posix_spawn_file_actions_init(&actions);
  posix_spawn_file_actions_addopen(&actions, 1, "/dev/null", O_WRONLY, 0);
  posix_spawn_file_actions_addopen(&actions, 2, "/dev/null", O_WRONLY, 0);

  // Put compare (and its GTK child) in a new process group so cancel can
  // signal the whole tree without leaving orphan overlays or open cameras.
  posix_spawnattr_t spawn_attr;
  posix_spawnattr_init(&spawn_attr);
  posix_spawnattr_setflags(&spawn_attr, POSIX_SPAWN_SETPGROUP);
  posix_spawnattr_setpgroup(&spawn_attr, 0);

  // Start the python subprocess
  int spawn_err = posix_spawnp(&child_pid, PYTHON_EXECUTABLE_PATH, &actions,
                               &spawn_attr, args.data(), nullptr);
  posix_spawnattr_destroy(&spawn_attr);
  posix_spawn_file_actions_destroy(&actions);

  if (spawn_err != 0) {
    syslog(LOG_ERR, "Can't spawn the ubuntu-hello process: %s (%d)",
           strerror(spawn_err), spawn_err);
    return PAM_SYSTEM_ERR;
  }

  // NOTE: We should replace mutex and condition_variable by atomic wait, but
  // it's too recent (C++20)
  std::mutex mutx;
  std::condition_variable convar;
  ConfirmationType confirmation_type(ConfirmationType::Unset);

  // This task wait for the status of the python subprocess (we don't want a
  // zombie process)
  optional_task<int> child_task([&]() -> int {
    int status = 0;
    waitpid(child_pid, &status, 0);
    {
      std::unique_lock<std::mutex> lock(mutx);
      if (confirmation_type == ConfirmationType::Unset) {
        confirmation_type = ConfirmationType::Ubuntu_Hello;
      }
    }
    convar.notify_one();

    return status;
  });
  child_task.activate();

  // This task waits for the password input (if the workaround wants it)
  optional_task<std::tuple<int, char *>> pass_task([&]() -> std::tuple<int, char *> {
    char *auth_tok_ptr = nullptr;
    int tok_res = pam_get_authtok(
        pamh, PAM_AUTHTOK, const_cast<const char **>(&auth_tok_ptr), nullptr);
    {
      std::unique_lock<std::mutex> lock(mutx);
      if (confirmation_type == ConfirmationType::Unset) {
        confirmation_type = ConfirmationType::Pam;
      }
    }
    convar.notify_one();

    return {tok_res, auth_tok_ptr};
  });

  // Concurrent password watch is workaround-gated ONLY.
  // HARD RULE: never OR in is_greeter_service() when workaround=off — that
  // aborts GDM user-selection → login. See AGENTS.md lifecycle CAUTION.
  const bool ask_pass = ask_auth_tok && workaround != Workaround::Off;
  if (ask_pass) {
    pass_task.activate();
  }

  // Wait for the end either of the child or the password input.
  // Unlock explicitly so clang-analyzer does not treat later I/O (e.g. TPM
  // fgets in try_set_keyring_authtok) as BlockInCriticalSection.
  std::unique_lock<std::mutex> lock(mutx);
  convar.wait(lock, [&]() -> bool {
    return confirmation_type != ConfirmationType::Unset;
  });
  lock.unlock();

  // The password has been entered or an error has occurred
  if (confirmation_type == ConfirmationType::Pam) {
    terminate_compare_group(child_pid, child_task);

    pass_task.stop(false);

    char *password = nullptr;
    std::tie(pam_res, password) = pass_task.get();

    if (pam_res != PAM_SUCCESS) {
      return pam_res;
    }

    // The password has been entered, we are passing it to PAM stack
    return PAM_IGNORE;
  }

  // The compare process has finished its execution
  child_task.stop(false);

  // Get python process status code
  int status = child_task.get();

  // If python process ran into a timeout
  // Do not send enter presses or terminate the PAM function, as the user might
  // still be typing their password
  if (WIFEXITED(status) && WEXITSTATUS(status) != EXIT_SUCCESS && ask_pass) {
    update_face_skip_from_status(username, status, skip_face_after_failure,
                                 face_skip_service);

    // Wait for the password to be typed
    pass_task.stop(false);

    char *password = nullptr;
    std::tie(pam_res, password) = pass_task.get();

    if (pam_res != PAM_SUCCESS) {
      return ubuntu_hello_status(username, status, config, conv_function);
    }

    // The password has been entered, we are passing it to PAM stack
    return PAM_IGNORE;
  }

  // We want to stop the password prompt, either by canceling the thread when
  // workaround is set to "native", or by emulating "Enter" input with
  // "input"
  dismiss_authtok_prompt(workaround, pass_task, conv_function);

  update_face_skip_from_status(username, status, skip_face_after_failure,
                               face_skip_service);

  if (WIFEXITED(status) && WEXITSTATUS(status) == EXIT_SUCCESS) {
    try_set_keyring_authtok(pamh, username);
  }

  return ubuntu_hello_status(username, status, config, conv_function);
}

// Called by PAM when a user needs to be authenticated, for example by running
// the sudo command
PAM_EXTERN auto pam_sm_authenticate(pam_handle_t *pamh, int flags, int argc,
                                    const char **argv) -> int {
  return identify(pamh, flags, argc, argv, true);
}

// Called by PAM when a session is started, such as by the su command
PAM_EXTERN auto pam_sm_open_session(pam_handle_t *pamh, int flags, int argc,
                                    const char **argv) -> int {
  return identify(pamh, flags, argc, argv, false);
}

// The functions below are required by PAM, but not needed in this module
PAM_EXTERN auto pam_sm_acct_mgmt(pam_handle_t *pamh, int flags, int argc,
                                 const char **argv) -> int {
  return PAM_IGNORE;
}
PAM_EXTERN auto pam_sm_close_session(pam_handle_t *pamh, int flags, int argc,
                                     const char **argv) -> int {
  return PAM_IGNORE;
}
PAM_EXTERN auto pam_sm_chauthtok(pam_handle_t *pamh, int flags, int argc,
                                 const char **argv) -> int {
  return PAM_IGNORE;
}
PAM_EXTERN auto pam_sm_setcred(pam_handle_t *pamh, int flags, int argc,
                               const char **argv) -> int {
  const char *username = nullptr;
  if (pam_get_user(pamh, &username, nullptr) != PAM_SUCCESS || username == nullptr) {
    return PAM_IGNORE;
  }

  // Validate username format
  if (!is_safe_username(username)) {
    syslog(LOG_ERR, "Invalid username format in pam_sm_setcred: %s", username);
    return PAM_IGNORE;
  }

  // Successful auth reached setcred — allow face again on next lock attempt.
  face_skip_clear(username);

  std::string pending_file = "/etc/ubuntu-hello/keyring-caching-pending/" + std::string(username);
  std::string tpm_pub = "/etc/ubuntu-hello/tpm-keys/" + std::string(username) + ".pub";
  std::string key_file = "/etc/ubuntu-hello/keyring-keys/" + std::string(username);
  
  struct stat st_pending{};
  struct stat st_tpm{};
  struct stat st_key{};
  bool is_pending = (stat(pending_file.c_str(), &st_pending) == 0);
  bool is_tpm = (stat(tpm_pub.c_str(), &st_tpm) == 0);
  bool is_key = (stat(key_file.c_str(), &st_key) == 0);
  
  if (!is_pending && !is_tpm && !is_key) {
    return PAM_IGNORE;
  }
  
  const char *password = nullptr;
  int pam_err = pam_get_item(pamh, PAM_AUTHTOK, reinterpret_cast<const void **>(&password));
  if (pam_err != PAM_SUCCESS || password == nullptr || strlen(password) == 0) {
    return PAM_IGNORE;
  }
  
  std::string cmd =
      "/usr/bin/ubuntu-hello keyring enable -U " + std::string(username);

  // Migrate legacy XOR software blobs (and refresh UH1/TPM) when PAM_AUTHTOK is available
  if (is_key) {
    std::ifstream kifs(key_file);
    std::string existing;
    if (kifs.is_open()) {
      std::getline(kifs, existing);
      if (!existing.empty() && existing.compare(0, 4, "UH1:") != 0) {
        syslog(LOG_INFO,
               "Migrating legacy keyring blob to UH1 for user %s via keyring enable",
               username);
      }
    }
  }

  FILE *file_pipe = popen_as_root(cmd, "w");
  if (file_pipe != nullptr) {
    fputs(password, file_pipe);
    fputc('\n', file_pipe);
    int status = pclose(file_pipe);
    if (status == 0) {
      if (is_pending) {
        unlink(pending_file.c_str());
        syslog(LOG_INFO, "Automatically cached and sealed keyring password for user %s", username);
      } else {
        syslog(LOG_INFO, "Automatically updated keyring password cache for user %s", username);
      }
    } else {
      syslog(LOG_ERR, "Failed to cache/seal password: ubuntu-hello keyring command exited with status %d", status);
    }
  } else {
    syslog(LOG_ERR, "Failed to run keyring helper to cache password");
  }
  
  return PAM_IGNORE;
}
