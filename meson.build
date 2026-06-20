project('ubuntu-hello', 'cpp', license: 'MIT', version: '1.0.4', meson_version: '>= 0.64.0')

dlibdatadir = get_option('dlib_data_dir') != '' ? get_option('dlib_data_dir') : join_paths(get_option('prefix'), get_option('datadir'), 'dlib-data')
confdir = get_option('config_dir') != '' ? get_option('config_dir') : join_paths(get_option('prefix'), get_option('sysconfdir'), 'ubuntu-hello')
usermodelsdir = get_option('user_models_dir') != '' ? get_option('user_models_dir') : join_paths(confdir, 'models')
logpath = get_option('log_path')
pythonpath = get_option('python_path')

config_path = join_paths(confdir, 'config.ini')

git_bin = find_program('git', required: false)
project_version = meson.project_version()
build_version = project_version

if git_bin.found()
  git_check = run_command(git_bin, '-C', meson.project_source_root(), 'rev-parse', '--is-inside-work-tree', check: false)
  if git_check.returncode() == 0 and git_check.stdout().strip() == 'true'
    git_desc = run_command(git_bin, '-C', meson.project_source_root(), 'describe', '--tags', '--always', '--dirty', check: false)
    if git_desc.returncode() == 0
      git_version_str = git_desc.stdout().strip()
      if git_version_str == 'v' + project_version or git_version_str == project_version
        build_version = git_version_str
      else
        build_version = git_version_str + ' (Development)'
      endif
    else
      build_version = 'v' + project_version + '-dev'
    endif
  endif
endif

paths_dict = {
    'config_dir': confdir,
    'dlib_data_dir': dlibdatadir,
    'user_models_dir': usermodelsdir,
    'log_path': logpath,
    'python_path': pythonpath,
    'version': build_version
}

# We need to keep this order beause ubuntu-hello-gtk defines the gtk script path which is used later in ubuntu-hello
subdir('ubuntu-hello-gtk')
subdir('ubuntu-hello')