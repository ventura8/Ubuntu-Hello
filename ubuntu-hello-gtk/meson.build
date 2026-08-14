if meson.is_subproject()
  project(
    'ubuntu-hello-gtk',
    license: 'MIT',
    version: run_command('python3', join_paths(meson.current_source_dir(), '..', 'scripts', 'read-version.py'), check: true).stdout().strip(),
    meson_version: '>= 0.64.0',
  )
endif

subdir('po')

datadir = get_option('prefix') / get_option('datadir') / 'ubuntu-hello-gtk'
py_conf = configuration_data(paths_dict)
py_conf.set('data_dir', datadir)
py_conf.set('localedir', get_option('prefix') / get_option('localedir'))
py_conf.set('gettext_package', 'ubuntu-hello-gtk')


py_paths = configure_file(
    input: 'src/paths.py.in',
    output: 'paths.py',
    configuration: py_conf,
)

py_i18n = configure_file(
    input: 'src/i18n.py.in',
    output: 'i18n.py',
    configuration: py_conf,
)

sources = files(
    'src/auth_helper.py',
    'src/authsticky.py',
    'src/init.py',
    'src/languages.py',
    'src/onboarding.py',
    'src/paths_factory.py',
    'src/preferences.py',
    'src/search_fuzzy.py',
    'src/tab_keyring.py',
    'src/tab_models.py',
    'src/tab_video.py',
    'src/theme_detect.py',
    'src/version_display.py',
    'src/window.py',
)

py = import('python').find_installation(
    # modules: ['gi', 'elevate']
)
py.dependency()

if get_option('install_in_site_packages')
    pysourcesinstalldir = join_paths(py.get_install_dir(), 'ubuntu-hello-gtk')
else
    pysourcesinstalldir = get_option('py_sources_dir') != '' ? get_option('py_sources_dir') / 'ubuntu-hello-gtk' : join_paths(get_option('prefix'), get_option('libdir'), 'ubuntu-hello-gtk')
endif

if get_option('install_in_site_packages')
    py.install_sources(
        sources,
        py_paths,
        py_i18n,
        subdir: 'ubuntu-hello-gtk',
        install_tag: 'py_sources',
    )
else
    install_data(
        sources,
        py_paths,
        py_i18n,
        install_dir: pysourcesinstalldir,
        install_mode: 'r--r--r--',
        install_tag: 'py_sources',
    )
endif

logos = files(
    'src/logo.png',
    'src/logo_about.png',
)
install_data(logos, install_dir: datadir)

install_data(
    'src/logo.png',
    install_dir: get_option('prefix') / get_option('datadir') / 'pixmaps',
    rename: 'ubuntu-hello-gtk.png',
    install_tag: 'meta'
)

i18n = import('i18n')
i18n.merge_file(
    input: 'ubuntu-hello-gtk.desktop.in',
    output: 'ubuntu-hello-gtk.desktop',
    type: 'desktop',
    po_dir: 'po',
    install: true,
    install_dir: get_option('prefix') / get_option('datadir') / 'applications',
    install_tag: 'meta',
)

interface_files = files(
    'src/main.glade',
    'src/onboarding.glade',
    'src/style.css',
)
install_data(interface_files, install_dir: datadir)

cli_path = join_paths(pysourcesinstalldir, 'init.py')
conf_data = configuration_data({ 'script_path': cli_path, 'python_path': py.full_path() })

bin_name = 'ubuntu-hello-gtk'
bin = configure_file(
    input: 'bin/ubuntu-hello-gtk.in',
    output: bin_name,
    configuration: conf_data
)
install_data(
  bin,
  install_mode: 'rwxr-xr-x',
  install_dir: get_option('prefix') / get_option('bindir'),
  install_tag: 'bin',
)

if get_option('with_polkit')
    polkit_config = configure_file(
        input: 'src/polkit/com.github.ventura8.ubuntu-hello-gtk.policy.in',
        output: 'com.github.ventura8.ubuntu-hello-gtk.policy',
        configuration: {'script_path': cli_path, 'python_path': py.full_path()}
    )
    install_data(
        polkit_config,
        install_dir: get_option('prefix') / get_option('datadir') / 'polkit-1' / 'actions',
        install_mode: 'rw-r--r--',
        install_tag: 'polkit',
    )
endif

# Install the post-install launcher for install.sh / dpkg postinst.
# Do NOT auto-run it from meson — that would race with install.sh and open
# the setup wizard twice. Callers launch it once after install completes.
install_data(
    'bin/run_after_install.py',
    install_dir: datadir,
    install_mode: 'rwxr-xr-x',
    install_tag: 'bin',
)
