inih_cpp = dependency('INIReader', fallback: ['inih', 'INIReader_dep'])
libevdev = dependency('libevdev')
libpam = meson.get_compiler('cpp').find_library('pam')
libcrypto = dependency('libcrypto')
threads = dependency('threads')

# GETTEXT_PACKAGE / LOCALEDIR for pam_ubuntu_hello.so (catalogs live in ../../po).
add_project_arguments(
	'-DGETTEXT_PACKAGE="ubuntu-hello"',
	'-DLOCALEDIR="@0@"'.format(get_option('prefix') / get_option('localedir')),
	language: 'cpp',
)

# Paths
paths_h = configure_file(
	input: 'paths.hh.in',
	output: 'paths.hh',
	configuration: pam_module_conf_data
)

pamdir = get_option('pam_dir') != '' ? get_option('pam_dir') : join_paths(get_option('prefix'), get_option('libdir'), 'security')

shared_library(
	'pam_ubuntu_hello',
	'main.cc',
	'enter_device.cc',
	'aes_gcm_uh1.cc',
	'face_skip.cc',
	cpp_args: [
		'-fstack-protector-strong',
		'-D_FORTIFY_SOURCE=2',
	],
	link_args: [
		'-Wl,-z,relro',
		'-Wl,-z,now',
	],
	dependencies: [
		libpam,
		inih_cpp,
		threads,
		libevdev,
		libcrypto,
	],
	link_depends: [
		paths_h,
	],
	install: true,
	install_dir: pamdir,
	install_tag: 'pam_module',
	name_prefix: ''
)

# Unit test for UH1 AES-256-GCM decrypt (software keyring fallback)
pam_aes_gcm_uh1_test = executable(
	'pam_aes_gcm_uh1_test',
	meson.project_source_root() / 'tests' / 'pam_aes_gcm_uh1_test.cc',
	'aes_gcm_uh1.cc',
	include_directories: include_directories('.'),
	dependencies: [libcrypto],
	install: false,
)
test(
	'pam-aes-gcm-uh1',
	pam_aes_gcm_uh1_test,
	args: [meson.current_build_dir()],
)

# Unit test for greeter face-skip markers (injectable base path)
pam_face_skip_test = executable(
	'pam_face_skip_test',
	meson.project_source_root() / 'tests' / 'pam_face_skip_test.cc',
	'face_skip.cc',
	include_directories: include_directories('.'),
	install: false,
)
test(
	'pam-face-skip',
	pam_face_skip_test,
	args: [meson.current_build_dir()],
)
