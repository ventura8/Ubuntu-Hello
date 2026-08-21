# openSUSE Leap 16.0 RPM spec for Ubuntu Hello (core + GTK subpackages)
# Version: rpmbuild -D "uh_version 1.1.3"

%global uh_version %{?uh_version}%{!?uh_version:0.0.0}
%global uh_release 1

Name:           ubuntu-hello
Version:        %{uh_version}
Release:        %{uh_release}%{?dist}
Summary:        Windows Hello style facial authentication for Linux
License:        MIT
URL:            https://github.com/ventura8/ubuntu-hello
Source0:        ubuntu-hello-%{version}.tar.gz

BuildRequires:  meson >= 0.64
BuildRequires:  ninja
BuildRequires:  gcc-c++
BuildRequires:  cmake
BuildRequires:  libboost_headers-devel
BuildRequires:  libboost_system-devel
BuildRequires:  libopenssl-devel
BuildRequires:  pam-devel
BuildRequires:  libinih-devel
BuildRequires:  libevdev-devel
BuildRequires:  gettext-tools
BuildRequires:  python313-devel
BuildRequires:  pkgconf-pkg-config

Requires:       python313
Requires:       python313-numpy
Requires:       python313-opencv
Requires:       python313-cryptography
Requires:       python313-pip
Requires:       cmake
Requires:       curl
Requires:       bzip2
Requires:       v4l-utils
Requires:       tpm2.0-tools
Requires:       pam
Recommends:     ubuntu-hello-gtk = %{version}-%{release}

%description
Ubuntu Hello provides Windows Hello style authentication for Linux using PAM,
facial recognition, and optional GTK settings.
Post-install configures PAM and installs dlib via pip when not already present.

%package -n ubuntu-hello-gtk
Summary:        GTK configuration GUI for Ubuntu Hello
Requires:       ubuntu-hello = %{version}-%{release}
Requires:       python313-gobject
Requires:       python313-pycairo
Requires:       gtk3
Requires:       polkit
Requires:       python313-Babel
Requires:       opencv

%description -n ubuntu-hello-gtk
Graphical settings and setup wizard for Ubuntu Hello facial authentication.

%prep
%setup -q -n ubuntu-hello-%{version}

%build
meson setup build \
    --prefix=/usr \
    --libdir=%{_libdir} \
    -Dpython_path=/usr/bin/python3.13 \
    -Ddlib_data_dir=/etc/ubuntu-hello/dlib-data \
    -Dconfig_dir=/etc/ubuntu-hello \
    -Duser_models_dir=/etc/ubuntu-hello/models \
    -Dinstall_pam_config=true \
    -Dwith_polkit=true \
    -Dfetch_dlib_data=false \
    -Dinih:with_INIReader=true
ninja -C build

%install
DESTDIR=%{buildroot} ninja -C build install
rm -rf %{buildroot}/etc/systemd/system/polkit-agent-helper@.service.d 2>/dev/null || true

%post
# Run on both fresh install ($1 == 1) and upgrade ($1 == 2): uh_package_configure
# is idempotent and upgrades may introduce new models/config/PAM/polkit setup.
. /usr/share/ubuntu-hello/package-configure.sh
uh_package_configure

%preun
if [ "$1" -eq 0 ]; then
    . /usr/share/ubuntu-hello/package-prerm.sh
    uh_package_prerm
fi

%post -n ubuntu-hello-gtk
if [ "$1" -eq 1 ]; then
    . /usr/share/ubuntu-hello/package-gtk-onboard.sh
    uh_package_gtk_onboard "ubuntu-hello-gtk.rpm"
fi

%files
%defattr(-,root,root,-)
/usr/bin/ubuntu-hello
%{_libdir}/security/pam_ubuntu_hello.so
%{_libdir}/ubuntu-hello/
/usr/share/bash-completion/completions/ubuntu-hello
/usr/share/man/man1/ubuntu-hello.1*
/usr/share/pam-configs/ubuntu-hello
/etc/pam.d/ubuntu-hello-verify
/usr/share/ubuntu-hello/
%dir /etc/ubuntu-hello
%config(noreplace) /etc/ubuntu-hello/config.ini
/etc/ubuntu-hello/dlib-data/
/usr/share/locale/*/LC_MESSAGES/ubuntu-hello.mo

%files -n ubuntu-hello-gtk
%defattr(-,root,root,-)
/usr/bin/ubuntu-hello-gtk
%{_libdir}/ubuntu-hello-gtk/
/usr/share/applications/ubuntu-hello-gtk.desktop
/usr/share/pixmaps/ubuntu-hello-gtk.png
/usr/share/ubuntu-hello-gtk/
/usr/share/polkit-1/actions/com.github.ventura8.ubuntu-hello-gtk.policy
/usr/share/locale/*/LC_MESSAGES/ubuntu-hello-gtk.mo

%changelog
* Wed Aug 19 2026 ventura8 <alexandrescu.sergiu@gmail.com> - %{uh_version}-%{uh_release}
- Multi-format release packaging
