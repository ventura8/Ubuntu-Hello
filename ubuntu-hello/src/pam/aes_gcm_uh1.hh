#ifndef AES_GCM_UH1_H_
#define AES_GCM_UH1_H_

#include <string>
#include <vector>

/** Default path for the 32-byte AES-256-GCM master key. */
inline constexpr const char *UH1_DEFAULT_MASTER_KEY_PATH =
    "/etc/ubuntu-hello/keyring-master.key";

/** Decode standard base64 (no newlines). Empty on failure. */
auto uh1_base64_decode(const std::string &input) -> std::vector<unsigned char>;

/**
 * Decrypt a UH1 AES-256-GCM keyring blob.
 * Format: "UH1:" + base64(nonce_12 || ciphertext || tag_16)
 * Returns empty string on failure; rejects legacy XOR ciphertext.
 *
 * @param blob           Ciphertext line (may include trailing whitespace)
 * @param master_key_path Path to 32-byte raw master key file
 */
auto aes_gcm_decrypt_uh1(const std::string &blob,
                         const std::string &master_key_path = UH1_DEFAULT_MASTER_KEY_PATH)
    -> std::string;

#endif  // AES_GCM_UH1_H_
