#include "aes_gcm_uh1.hh"

#include <array>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

#include <openssl/evp.h>
#include <openssl/rand.h>

namespace {

auto fail(const char *msg) -> int {
  std::cerr << "FAIL: " << msg << "\n";
  return 1;
}

auto b64_encode(const unsigned char *data, size_t len) -> std::string {
  static constexpr std::string_view table =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  std::string out;
  out.reserve(((len + 2) / 3) * 4);
  for (size_t idx = 0; idx < len; idx += 3) {
    unsigned int chunk = static_cast<unsigned int>(data[idx]) << 16;
    if (idx + 1 < len) {
      chunk |= static_cast<unsigned int>(data[idx + 1]) << 8;
    }
    if (idx + 2 < len) {
      chunk |= static_cast<unsigned int>(data[idx + 2]);
    }
    out.push_back(table[(chunk >> 18) & 63]);
    out.push_back(table[(chunk >> 12) & 63]);
    out.push_back(idx + 1 < len ? table[(chunk >> 6) & 63] : '=');
    out.push_back(idx + 2 < len ? table[chunk & 63] : '=');
  }
  return out;
}

auto aes_gcm_encrypt_uh1(const std::string &plaintext, const std::string &key_path)
    -> std::string {
  std::ifstream key_ifs(key_path, std::ios::binary);
  std::vector<unsigned char> key(32);
  key_ifs.read(reinterpret_cast<char *>(key.data()), 32);
  if (key_ifs.gcount() != 32) {
    return "";
  }

  std::array<unsigned char, 12> nonce{};
  if (RAND_bytes(nonce.data(), static_cast<int>(nonce.size())) != 1) {
    return "";
  }

  EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
  if (ctx == nullptr) {
    return "";
  }

  std::vector<unsigned char> ciphertext(plaintext.size());
  int out_len = 0;
  int total = 0;
  bool succeeded = false;
  std::array<unsigned char, 16> tag{};

  do {
    if (EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) != 1) {
      break;
    }
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, nullptr) != 1) {
      break;
    }
    if (EVP_EncryptInit_ex(ctx, nullptr, nullptr, key.data(), nonce.data()) != 1) {
      break;
    }
    if (!plaintext.empty()) {
      if (EVP_EncryptUpdate(ctx, ciphertext.data(), &out_len,
                            reinterpret_cast<const unsigned char *>(plaintext.data()),
                            static_cast<int>(plaintext.size())) != 1) {
        break;
      }
      total = out_len;
    }
    if (EVP_EncryptFinal_ex(ctx, ciphertext.data() + total, &out_len) != 1) {
      break;
    }
    total += out_len;
    if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, tag.data()) != 1) {
      break;
    }
    succeeded = true;
  } while (false);

  EVP_CIPHER_CTX_free(ctx);
  if (!succeeded) {
    return "";
  }

  std::vector<unsigned char> packed;
  packed.insert(packed.end(), nonce.begin(), nonce.end());
  packed.insert(packed.end(), ciphertext.begin(), ciphertext.begin() + total);
  packed.insert(packed.end(), tag.begin(), tag.end());
  return "UH1:" + b64_encode(packed.data(), packed.size());
}

} // namespace

auto main(int argc, char **argv) -> int {
  if (argc < 2) {
    return fail("usage: pam_aes_gcm_uh1_test <tmpdir>");
  }
  const std::string tmpdir = argv[1];
  const std::string key_path = tmpdir + "/keyring-master.key";

  {
    std::ofstream ofs(key_path, std::ios::binary);
    const std::string key(32, '\x42');
    ofs.write(key.data(), 32);
  }

  const std::string blob = aes_gcm_encrypt_uh1("s3cret-pass", key_path);
  if (blob.empty() || blob.compare(0, 4, "UH1:") != 0) {
    return fail("encrypt did not produce UH1 blob");
  }
  const std::string plain = aes_gcm_decrypt_uh1(blob, key_path);
  if (plain != "s3cret-pass") {
    return fail("decrypt round-trip mismatch");
  }

  if (!aes_gcm_decrypt_uh1("aabbccddeeff", key_path).empty()) {
    return fail("legacy XOR should be rejected");
  }

  if (!aes_gcm_decrypt_uh1("UH1:!!!!", key_path).empty()) {
    return fail("bad base64 should fail");
  }
  if (!aes_gcm_decrypt_uh1("UH1:YQ==", key_path).empty()) {
    return fail("short blob should fail");
  }

  const std::string other_key = tmpdir + "/other.key";
  {
    std::ofstream ofs(other_key, std::ios::binary);
    const std::string key(32, '\x11');
    ofs.write(key.data(), 32);
  }
  if (!aes_gcm_decrypt_uh1(blob, other_key).empty()) {
    return fail("wrong key should fail auth");
  }

  if (!aes_gcm_decrypt_uh1(blob, tmpdir + "/missing.key").empty()) {
    return fail("missing key should fail");
  }

  std::cout << "pam_aes_gcm_uh1_test: OK\n";
  return 0;
}
