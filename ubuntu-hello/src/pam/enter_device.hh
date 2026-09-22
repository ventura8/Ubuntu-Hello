#ifndef ENTER_DEVICE_H_
#define ENTER_DEVICE_H_

#include <libevdev/libevdev-uinput.h>
#include <libevdev/libevdev.h>
#include <memory>
#include <stdexcept>
#include <string>

// Thrown by EnterDevice when libevdev/uinput refuses. Dedicated so the PAM
// stack can catch exactly this and fall back to "wait for the user to press
// Enter" without also swallowing unrelated runtime_errors.
class EnterDeviceError : public std::runtime_error {
public:
  using std::runtime_error::runtime_error;
};

class EnterDevice {
  std::unique_ptr<struct libevdev, decltype(&libevdev_free)> raw_device;
  std::unique_ptr<struct libevdev_uinput, decltype(&libevdev_uinput_destroy)>
      raw_uinput_device;

public:
  EnterDevice();
  void send_enter_press() const;
  ~EnterDevice() = default;
};

#endif // ENTER_DEVICE_H
