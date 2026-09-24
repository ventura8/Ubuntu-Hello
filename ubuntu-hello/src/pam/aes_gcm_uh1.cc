#include "aes_gcm_uh1.hh"

#include <fstream>
#include <syslog.h>

#include <openssl/bio.h>
#include <openssl/evp.h>

auto uh1_base64_decode(std::string_view input) -> std::vector<unsigned char> {
  BIO *b64 = BIO_new(BIO_f_base64());
  if (b64 == nullptr) {
    return {};
  }
  BIO_set_flags(b64, BIO_FLAGS_BASE64_NO_NL);
  BIO *bio = BIO_new_mem_buf(input.data(), static_cast<int>(input.size()));
  if (bio == nullptr) {
    BIO_free(b64);
    return {};
  }
  bio = BIO_push(b64, bio);

  std::vector<unsigned char> out(input.size());
  const int decoded_len = BIO_read(bio, out.data(), static_cast<int>(out.size()));
  BIO_free_all(bio);
  if (decoded_len <= 0) {
    return {};
  }
  out.resize(static_cast<size_t>(decoded_len));
  return out;
}

namespace {

constexpr size_t kNonceLen = 12;
constexpr size_t kTagLen = 16;
constexpr size_t kKeyLen = 32;

// Trailing whitespace from however the blob was stored/echoed.
auto rstrip(std::string value) -> std::string {
  while (!value.empty() && (value.back() == '\n' || value.back() == '\r' ||
                            value.back() == ' ' || value.back() == '\t')) {
    value.pop_back();
  }
  return value;
}

// Read exactly kKeyLen bytes of master key, or return an empty vector.
auto read_master_key(const std::string &path) -> std::vector<unsigned char> {
  std::ifstream key_ifs(path, std::ios::binary);
  if (!key_ifs.is_open()) {
    syslog(LOG_ERR, "Failed to open keyring master key");
    return {};
  }
  std::vector<unsigned char> key(kKeyLen);
  key_ifs.read(reinterpret_cast<char *>(key.data()),
               static_cast<std::streamsize>(kKeyLen));
  if (key_ifs.gcount() != static_cast<std::streamsize>(kKeyLen)) {
    syslog(LOG_ERR, "Keyring master key has invalid length");
    return {};
  }
  return key;
}

// The EVP sequence itself. Split out so each failure is a plain early return
// instead of a break out of a do/while(false), and so the caller owns exactly
// one EVP_CIPHER_CTX_free.
auto gcm_open(EVP_CIPHER_CTX *ctx, const std::vector<unsigned char> &key,
              const unsigned char *nonce, const unsigned char *ciphertext,
              int ciphertext_len, const unsigned char *tag,
              std::string &plaintext) -> bool {
  if (EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) != 1) {
    return false;
  }
  if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN,
                          static_cast<int>(kNonceLen), nullptr) != 1) {
    return false;
  }
  if (EVP_DecryptInit_ex(ctx, nullptr, nullptr, key.data(), nonce) != 1) {
    return false;
  }

  plaintext.resize(static_cast<size_t>(ciphertext_len > 0 ? ciphertext_len : 0));
  int out_len = 0;
  if (ciphertext_len > 0 &&
      EVP_DecryptUpdate(ctx, reinterpret_cast<unsigned char *>(plaintext.data()),
                        &out_len, ciphertext, ciphertext_len) != 1) {
    return false;
  }
  int total = out_len;

  if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, static_cast<int>(kTagLen),
                          const_cast<unsigned char *>(tag)) != 1) {
    return false;
  }

  int final_len = 0;
  if (EVP_DecryptFinal_ex(ctx,
                          reinterpret_cast<unsigned char *>(plaintext.data()) + total,
                          &final_len) != 1) {
    syslog(LOG_ERR, "AES-GCM authentication failed for keyring blob");
    return false;
  }
  total += final_len;
  plaintext.resize(static_cast<size_t>(total));
  return true;
}

} // namespace

auto aes_gcm_decrypt_uh1(const std::string &blob_in,
                         const std::string &master_key_path) -> std::string {
  static const std::string kPrefix = "UH1:";

  const std::string blob = rstrip(blob_in);
  if (blob.compare(0, kPrefix.size(), kPrefix) != 0) {
    syslog(LOG_ERR, "Rejecting legacy or unsupported keyring blob (expected UH1: prefix)");
    return "";
  }

  const std::vector<unsigned char> raw = uh1_base64_decode(std::string_view{blob}.substr(kPrefix.size()));
  if (raw.size() < kNonceLen + kTagLen) {
    syslog(LOG_ERR, "Malformed UH1 keyring blob");
    return "";
  }

  const std::vector<unsigned char> key = read_master_key(master_key_path);
  if (key.empty()) {
    return "";
  }

  const unsigned char *nonce = raw.data();
  const unsigned char *tag = raw.data() + (raw.size() - kTagLen);
  const unsigned char *ciphertext = raw.data() + kNonceLen;
  const auto ciphertext_len = static_cast<int>(raw.size() - kNonceLen - kTagLen);

  EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
  if (ctx == nullptr) {
    return "";
  }

  std::string plaintext;
  const bool succeeded =
      gcm_open(ctx, key, nonce, ciphertext, ciphertext_len, tag, plaintext);
  EVP_CIPHER_CTX_free(ctx);

  if (!succeeded) {
    return "";
  }
  return plaintext;
}
