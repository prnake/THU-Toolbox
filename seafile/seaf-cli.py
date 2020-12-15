#!/usr/bin/env python3
#-*- coding:utf-8 -*-
# pylint: disable=E1121

'''

seaf-cli is command line interface for seafile client.

Subcommands:

    init:             create config files for seafile client
    start:            start and run seafile client as daemon
    stop:             stop seafile client
    list:             list local libraries
    status:           show syncing status
    download:         download a library from seafile server
                          (using libary id)
    download-by-name: download a library from seafile server
                          (using library name)
    sync:             synchronize an existing folder with a library in
                          seafile server
    desync:           desynchronize a library with seafile server
    create:           create a new library


Detail
======

Seafile client stores all its configure information in a config dir. The default location is `~/.ccnet`. All the commands below accept an option `-c <config-dir>`.

init
----
Initialize seafile client. This command initializes the config dir. It also creates sub-directories `seafile-data` and `seafile` under `parent-dir`. `seafile-data` is used to store internal data, while `seafile` is used as the default location put downloaded libraries.

    seaf-cli init [-c <config-dir>] -d <parent-dir>

start
-----
Start seafile client. This command starts `seaf-daemon`, which manages all the files.

    seaf-cli start [-c <config-dir>]

stop
----
Stop seafile client.

    seaf-cli stop [-c <config-dir>]


Download by id
--------
Download a library from seafile server (using library id)

    seaf-cli download -l <library-id> -s <seahub-server-url> -d <parent-directory> -o token [-u <username> -p <password> -a <2fa-code>]


Download by name
--------
Download a library from seafile server (using library name)

    seaf-cli download -L <library-name> -s <seahub-server-url> -d <parent-directory> -o token [-u <username> -p <password> -a <2fa-code>]


sync
----
Synchronize a library with an existing folder.

    seaf-cli sync -l <library-id> -s <seahub-server-url> -d <existing-folder> -o token [-u <username> -p <password> -a <2fa-code>]

desync
------
Desynchronize a library from seafile server

    seaf-cli desync -d <existing-folder>

create
------
Create a new library

    seaf-cli create -s <seahub-server-url> -n <library-name> -o token [-u <username> -p <password> -a <2fa-code>] -t <description> [-e <library-password>]

'''
import argparse
import os
import json
import subprocess
import re
import sys
import time
import getpass
import random
import urllib.request, urllib.parse, urllib.error
import urllib.request, urllib.error, urllib.parse
from urllib.parse import urlparse

from os.path import abspath, dirname, exists, isdir, join

import seafile

if 'HOME' in os.environ:
    DEFAULT_CONF_DIR = "%s/.ccnet" % os.environ['HOME']
    DEFAULT_USER_CONF_DIR = "%s/.seafile.conf" % os.environ['HOME']    
else:
    DEFAULT_CONF_DIR = None
    DEFAULT_USER_CONF_DIR = None

seafile_datadir = None
seafile_worktree = None


def _check_seafile():
    ''' Check seafile daemon have been installed '''

    dirs = os.environ['PATH'].split(':')
    def exist_in_path(prog):
        ''' Check whether 'prog' exists in system path '''
        for d in dirs:
            if d == '':
                continue
            path = join(d, prog)
            if exists(path):
                return True

    progs = ['seaf-daemon']

    for prog in progs:
        if not exist_in_path(prog):
            print("%s not found in PATH. Have you installed seafile?" % prog)
            sys.exit(1)

def get_rpc_client(confdir):
    return seafile.RpcClient(join(seafile_datadir, 'seafile.sock'))

def _config_valid(conf):
    ''' Check config directory valid '''

    if not exists(conf) or not isdir(conf):
        print("%s not exists" % conf)
        return False

    seafile_ini = conf + "/seafile.ini"
    if not exists(seafile_ini):
        print("Could not load %s" % seafile_ini)
        return False

    with open(seafile_ini) as f:
        for line in f:
            global seafile_datadir, seafile_worktree
            seafile_datadir = line.strip()
            seafile_worktree = join(
                dirname(seafile_datadir), "seafile")
            break

    if not seafile_datadir or not seafile_worktree:
        print("Could not load seafile_datadir and seafile_worktree")
        return False
    return True


def _conf_dir(args):
    ''' Determine and return the value of conf_dir '''
    conf_dir = DEFAULT_CONF_DIR
    if args.confdir:
        conf_dir = args.confdir
    conf_dir = abspath(conf_dir)

    if not _config_valid(conf_dir):
        print("Invalid config directory")
        sys.exit(1)
    else:
        get_device_id(conf_dir)
        return conf_dir

def _user_config_valid(conf):
    if exists(conf):
        return True
    return False

def _parse_user_config(conf):
    try:
        from configparser import ConfigParser
        from configparser import NoOptionError
    except ImportError:
        from ConfigParser import ConfigParser
        from ConfigParser import NoOptionError
        
    cfg = ConfigParser()
    cfg.read(conf)
    if len(cfg.sections()) < 1 or cfg.sections()[0] != 'account':
        return None, None
    try:
        server = cfg.get('account', 'server')
        user = cfg.get('account', 'user')
        return server,user
    except NoOptionError:
        return None, None

def run_argv(argv, cwd=None, env=None, suppress_stdout=False, suppress_stderr=False):
    '''Run a program and wait it to finish, and return its exit code. The
    standard output of this program is supressed.

    '''
    with open(os.devnull, 'w') as devnull:
        if suppress_stdout:
            stdout = devnull
        else:
            stdout = sys.stdout

        if suppress_stderr:
            stderr = devnull
        else:
            stderr = sys.stderr

        proc = subprocess.Popen(argv,
                                cwd=cwd,
                                stdout=stdout,
                                stderr=stderr,
                                env=env)
        return proc.wait()

def get_env():
    env = dict(os.environ)
    ld_library_path = os.environ.get('SEAFILE_LD_LIBRARY_PATH', '')
    if ld_library_path:
        env['LD_LIBRARY_PATH'] = ld_library_path

    return env

def urlopen(url, data=None, headers=None):
    if data:
        data = urllib.parse.urlencode(data).encode('utf-8')
    headers = headers or {}
    req = urllib.request.Request(url, data=data, headers=headers)
    resp = urllib.request.urlopen(req)

    return resp.read()

SEAF_CLI_VERSION = ""

def randstring(size):
    random.seed(time.time())
    s = ''
    while len(s) < size:
        s += '%x' % random.randint(0, 255)
    return s[:size]

device_id = None
def get_device_id(conf_dir):
    global device_id
    if device_id:
        return device_id

    idfile = join(seafile_datadir, 'id')
    ccnet_conf = join(conf_dir, 'ccnet.conf')
    if exists(idfile):
        with open(idfile, 'r') as fp:
            device_id = fp.read().strip()
            return device_id

    # Id file doesn't exist. We either migrate it from ccnet.conf ID
    # (for existing data), or create it.

    if exists(ccnet_conf):
        # migrate from existing ccnet.conf ID
        with open(ccnet_conf, 'r') as fp:
            for line in fp:
                m = re.search('ID = (.*)', line)
                if m:
                    device_id = m.group(1)
                    print('Migrating device id from ccnet conf')
                    break
    if not device_id:
        # create a new id
        print('New device id created')
        device_id = randstring(40)
    with open(idfile, 'w') as fp:
        fp.write(device_id)
    return device_id

def get_token(url, username, password, tfa, conf_dir):
    platform = 'linux'
    device_id = get_device_id(conf_dir)
    device_name = 'terminal-' + os.uname()[1]
    client_version = SEAF_CLI_VERSION
    platform_version = ''
    data = {
        'username': username,
        'password': password,
        'platform': platform,
        'device_id': device_id,
        'device_name': device_name,
        'client_version': client_version,
        'platform_version': platform_version,
    }
    if tfa:
        headers = {
            'X-SEAFILE-OTP': tfa,
        }
    else:
        headers = None
    token_json = urlopen("%s/api2/auth-token/" % url, data=data, headers=headers)
    tmp = json.loads(token_json.decode('utf8'))
    token = tmp['token']
    return token

def get_repo_download_info(url, token):
    headers = { 'Authorization': 'Token %s' % token }
    repo_info = urlopen(url, headers=headers)
    return json.loads(repo_info.decode('utf8'))

def seaf_init(args):
    ''' Initialize config directories'''

    ccnet_conf_dir = DEFAULT_CONF_DIR
    if args.confdir:
        ccnet_conf_dir = args.confdir
    if args.dir:
        seafile_path = args.dir
    else:
        print("Must specify the parent path for put seafile-data")
        sys.exit(0)
    seafile_path = abspath(seafile_path)

    if exists(ccnet_conf_dir):
        print("%s already exists" % ccnet_conf_dir)
        sys.exit(0)

    os.mkdir(ccnet_conf_dir)
    logsdir = join(ccnet_conf_dir, 'logs')
    if not exists(logsdir):
        os.mkdir(logsdir)

    if not exists(seafile_path):
        print("%s not exists" % seafile_path)
        sys.exit(0)
    seafile_ini = ccnet_conf_dir + "/seafile.ini"
    seafile_data = seafile_path + "/seafile-data"
    with open(seafile_ini, 'w') as fp:
        fp.write(seafile_data)
    if not exists(seafile_data):
        os.mkdir(seafile_data)
    print("Writen seafile data directory %s to %s" % (seafile_data, seafile_ini))


def seaf_start_all(args):
    ''' Start seafile daemon '''
    seaf_start_seafile(args)

def seaf_start_seafile(args):
    ''' start seafile daemon '''

    conf_dir = _conf_dir(args)

    print("Starting seafile daemon ...")

    cmd = [ "seaf-daemon", "--daemon", "-c", conf_dir, "-d", seafile_datadir,
            "-w", seafile_worktree ]
    if run_argv(cmd, env=get_env()) != 0:
        print('Failed to start seafile daemon')
        sys.exit(1)

    print("Started: seafile daemon ...")

def seaf_stop(args):
    '''Stop seafile daemon '''

    conf_dir = _conf_dir(args)

    seafile_rpc = get_rpc_client(conf_dir)
    try:
        # TODO: add shutdown rpc in seaf-daemon
        seafile_rpc.shutdown()
    except:
        # ignore NetworkError("Failed to read from socket")
        pass


def seaf_list(args):
    '''List local libraries'''

    conf_dir = _conf_dir(args)

    seafile_rpc = get_rpc_client(conf_dir)
    repos = seafile_rpc.get_repo_list(-1, -1)
    print("Name\tID\tPath")
    for repo in repos:
        print(repo.name, repo.id, repo.worktree)


def seaf_list_remote(args):
    '''List remote libraries'''

    conf_dir = _conf_dir(args)

    server_from_config, user_from_config = None, None
    user_config_dir = args.C
    if not user_config_dir:
        user_config_dir = DEFAULT_USER_CONF_DIR
    else:
        user_config_dir = abspath(user_config_dir)
    if _user_config_valid(user_config_dir):
        server_from_config, user_from_config = _parse_user_config(user_config_dir)    

    url = args.server        
    if not url and server_from_config:
        url = server_from_config
    if not url:
        print("Seafile server url need to be presented")
        sys.exit(1)

    seafile_rpc = get_rpc_client(conf_dir)
    
    token = args.token
    if not token:
        username = args.username
        if not username and user_from_config:
            username = user_from_config;
        if not username:
            username = input("Enter username: ")
        password = args.password
        if not password:
            password = getpass.getpass("Enter password for user %s : " % username)
        tfa = args.tfa
      
        # curl -d 'username=<USERNAME>&password=<PASSWORD>' http://127.0.0.1:8000/api2/auth-token
        token = get_token(url, username, password, tfa, conf_dir)
    
    repos = get_repo_download_info("%s/api2/repos/" % (url), token)

    printed = {}

    print("Name\tID")
    for repo in repos:
        if repo['id'] in printed:
            continue

        printed[repo['id']] = repo['id']
        print(repo['name'], repo['id'])


def get_base_url(url):
    parse_result = urlparse(url)
    scheme = parse_result.scheme
    netloc = parse_result.netloc

    if scheme and netloc:
        return '%s://%s' % (scheme, netloc)

    return None

def seaf_download(args):
    '''Download a library from seafile server '''

    conf_dir = _conf_dir(args)

    repo = args.library
    if not repo:
        print("Library id is required")
        sys.exit(1)

    server_from_config, user_from_config = None, None        
    user_config_dir = args.C
    if not user_config_dir:
        user_config_dir = DEFAULT_USER_CONF_DIR
    else:
        user_config_dir = abspath(user_config_dir)
    if _user_config_valid(user_config_dir):
        server_from_config, user_from_config = _parse_user_config(user_config_dir)

    url = args.server
    if not url and server_from_config:
        url = server_from_config
    if not url:
        print("Seafile server url need to be presented")
        sys.exit(1)

    download_dir = seafile_worktree
    if args.dir:
        download_dir = abspath(args.dir)


    seafile_rpc = get_rpc_client(conf_dir)
    
    token = args.token
    if not token:
        username = args.username
        if not username and user_from_config:
            username = user_from_config
        if not username:
            username = input("Enter username: ")
        password = args.password
        if not password:
            password = getpass.getpass("Enter password for user %s : " % username)
        tfa = args.tfa
      
        # curl -d 'username=<USERNAME>&password=<PASSWORD>' http://127.0.0.1:8000/api2/auth-token
        token = get_token(url, username, password, tfa, conf_dir)

    tmp = get_repo_download_info("%s/api2/repos/%s/download-info/" % (url, repo), token)

    encrypted = tmp['encrypted']
    magic = tmp.get('magic', None)
    enc_version = tmp.get('enc_version', None)
    random_key = tmp.get('random_key', None)

    clone_token = tmp['token']
    email = tmp['email']
    repo_name = tmp['repo_name']
    version = tmp.get('repo_version', 0)
    repo_salt = tmp.get('salt', None)
    permission = tmp.get('permission', None)

    is_readonly = 0
    if permission == 'r':
        is_readonly = 1
    
    more_info = None
    more_info_dict = {}
    base_url = get_base_url(url)
    if base_url:
        more_info_dict.update({'server_url': base_url})
    if repo_salt:
        more_info_dict.update({'repo_salt': repo_salt})
    more_info_dict.update({'is_readonly': is_readonly})
    more_info = json.dumps(more_info_dict)

    print("Starting to download ...")
    print("Library %s will be downloaded to %s" % (repo, download_dir))
    if encrypted == 1:
        repo_passwd = args.libpasswd if args.libpasswd else getpass.getpass("Enter password for the library: ")
    else:
        repo_passwd = None

    seafile_rpc.download(repo,
                         version,
                         repo_name,
                         download_dir,
                         clone_token,
                         repo_passwd, magic,
                         email, random_key, enc_version, more_info)


def seaf_download_by_name(args):
    '''Download a library defined by name from seafile server'''
    id = None

    conf_dir = _conf_dir(args)

    libraryname = args.libraryname
    if not libraryname:
        print("Library name is required")
        sys.exit(1)

    server_from_config, user_from_config = None, None
    user_config_dir = args.C
    if not user_config_dir:
        user_config_dir = DEFAULT_USER_CONF_DIR
    else:
        user_config_dir = abspath(user_config_dir)
    if _user_config_valid(user_config_dir):
        server_from_config, user_from_config = _parse_user_config(user_config_dir)        

    url = args.server        
    if not url and server_from_config:
        url = server_from_config
    if not url:
        print("Seafile server url need to be presented")
        sys.exit(1)

    seafile_rpc = get_rpc_client(conf_dir)
  
    token = args.token
    if not token:
        username = args.username
        if not username and user_from_config:
            username = user_from_config;
        if not username:
            username = input("Enter username: ")
            args.username = username
        password = args.password
        if not password:
            password = getpass.getpass("Enter password for user %s : " % username)
            args.password = password
        tfa = args.tfa
      
        # curl -d 'username=<USERNAME>&password=<PASSWORD>' http://127.0.0.1:8000/api2/auth-token
        token = get_token(url, username, password, tfa, conf_dir)

    tmp = get_repo_download_info("%s/api2/repos/" % (url), token)

    for i in tmp:
        if libraryname == i['name']:
             id = i['id']

    if not id:
        print("Defined library name not found")
        sys.exit(1)

    args.library = id
    seaf_download(args)


def seaf_sync(args):
    ''' synchronize a library from seafile server '''

    conf_dir = _conf_dir(args)

    repo = args.library
    if not repo:
        print("Library id is required")
        sys.exit(1)

    server_from_config, user_from_config = None, None
    user_config_dir = args.C
    if not user_config_dir:
        user_config_dir = DEFAULT_USER_CONF_DIR
    else:
        user_config_dir = abspath(user_config_dir)
    if _user_config_valid(user_config_dir):
        server_from_config, user_from_config = _parse_user_config(user_config_dir)

    url = args.server
    if not url and server_from_config:
        url = server_from_config
    if not url:
        print("Seafile server url is required")
        sys.exit(1)

    folder = args.folder
    if not folder:
        print("The local directory is required")
        sys.exit(1)

    folder = abspath(folder)
    if not exists(folder):
        print("The local directory does not exists")
        sys.exit(1)

    seafile_rpc = get_rpc_client(conf_dir)
    
    token = args.token
    if not token:
        username = args.username
        if not username and user_from_config:
            username = user_from_config;
        if not username:
            username = input("Enter username: ")
        password = args.password
        if not password:
            password = getpass.getpass("Enter password for user %s : " % username)
        tfa = args.tfa
        token = get_token(url, username, password, tfa, conf_dir)
    
    tmp = get_repo_download_info("%s/api2/repos/%s/download-info/" % (url, repo), token)

    encrypted = tmp['encrypted']
    magic = tmp.get('magic', None)
    enc_version = tmp.get('enc_version', None)
    random_key = tmp.get('random_key', None)

    clone_token = tmp['token']
    email = tmp['email']
    repo_name = tmp['repo_name']
    version = tmp.get('repo_version', 0)
    repo_salt =  tmp.get('salt', None)
    permission = tmp.get('permission', None)

    is_readonly = 0
    if permission == 'r':
        is_readonly = 1
    
    more_info = None
    more_info_dict = {}
    base_url = get_base_url(url)
    if base_url:
        more_info_dict.update({'server_url': base_url})
    if repo_salt:
        more_info_dict.update({'repo_salt': repo_salt})
    more_info_dict.update({'is_readonly': is_readonly})
    more_info = json.dumps(more_info_dict)

    print("Starting to download ...")
    if encrypted == 1:
        repo_passwd = args.libpasswd if args.libpasswd else getpass.getpass("Enter password for the library: ")
    else:
        repo_passwd = None

    seafile_rpc.clone(repo,
                      version,
                      repo_name,
                      folder,
                      clone_token,
                      repo_passwd, magic,
                      email, random_key, enc_version, more_info)


def seaf_desync(args):
    '''Desynchronize a library from seafile server'''

    conf_dir = _conf_dir(args)

    repo_path = args.folder
    if not repo_path:
        print("Must specify the local path of the library")
        sys.exit(1)
    repo_path = abspath(repo_path)

    seafile_rpc = get_rpc_client(conf_dir)

    repos = seafile_rpc.get_repo_list(-1, -1)
    repo = None
    for r in repos:
        if r.worktree == repo_path:
            repo = r
            break

    if repo is not None:
        if sys.version_info[0] == 2:
            repo.name = repo.name.encode('utf8')
        print("Desynchronize %s" % repo.name)
        seafile_rpc.remove_repo(repo.id)
    else:
        print("%s is not a library" % args.folder)


def seaf_config(args):
    '''Configure the seafile client'''

    conf_dir = _conf_dir(args)

    config_key = args.key
    if not config_key:
        print("Must specify configuration key")
        sys.exit(1)

    config_value = args.value

    seafile_rpc = get_rpc_client(conf_dir)

    if config_value:
        # set configuration key
        seafile_rpc.seafile_set_config(config_key, config_value)
    else:
        # print configuration key
        val = seafile_rpc.seafile_get_config(config_key)
        print("%s = %s" % (config_key, val))


def seaf_status(args):
    '''Show status'''

    conf_dir = _conf_dir(args)

    seafile_rpc = get_rpc_client(conf_dir)

    tasks = seafile_rpc.get_clone_tasks()
    print('# {:<50s}\t{:<20s}\t{:<20s}'.format('Name', 'Status', 'Progress'))
    for task in tasks:
        if sys.version_info[0] == 2:
            task.repo_name = task.repo_name.encode('utf8')
        if task.state == "fetch":
            tx_task = seafile_rpc.find_transfer_task(task.repo_id)
            try:
                print('{:<50s}\t{:<20s}\t{:<.1f}%, {:<.1f}KB/s'.format(task.repo_name, 'downloading',
                                                                       tx_task.block_done / tx_task.block_total * 100,
                                                                       tx_task.rate / 1024.0))
            except ZeroDivisionError: pass
        elif task.state == "error":
            err = seafile_rpc.sync_error_id_to_str(task.error)
            print('{:<50s}\t{:<20s}\t{:<20s}'.format(task.repo_name, 'error', err))            
        elif task.state == 'done':
            # will be shown in repo status
            pass
        else:
            print('{:<50s}\t{:<20s}'.format(task.repo_name, task.state))

    repos = seafile_rpc.get_repo_list(-1, -1)
    for repo in repos:
        auto_sync_enabled = seafile_rpc.is_auto_sync_enabled()
        if not auto_sync_enabled or not repo.auto_sync:
            print('{:<50s}\t{:<20s}'.format(repo.name, 'auto sync disabled'))
            continue

        task = seafile_rpc.get_repo_sync_task(repo.id)
        if task is None:
            print('{:<50s}\t{:<20s}'.format(repo.name, 'waiting for sync'))
        elif task.state == 'uploading':
            tx_task = seafile_rpc.find_transfer_task(repo.id)
            try:
                print('{:<50s}\t{:<20s}\t{:<.1f}%, {:<.1f}KB/s'.format(repo.name, 'uploading',
                                                                       tx_task.block_done / tx_task.block_total * 100,
                                                                       tx_task.rate / 1024.0))
            except ZeroDivisionError: pass
        elif task.state == 'downloading':
            tx_task = seafile_rpc.find_transfer_task(repo.id)
            try:
                if tx_task.rt_state == 'data':
                    print('{:<50s}\t{:<20s}\t{:<.1f}%, {:<.1f}KB/s'.format(repo.name, 'downloading files',
                                                                           tx_task.block_done / tx_task.block_total * 100,
                                                                           tx_task.rate / 1024.0))
                if tx_task.rt_state == 'fs':
                    print('{:<50s}\t{:<20s}\t{:<.1f}%'.format(repo.name, 'downloading file list',
                                                              tx_task.fs_objects_done / tx_task.fs_objects_total * 100))
            except ZeroDivisionError: pass
        elif task.state == 'error':
            err = seafile_rpc.sync_error_id_to_str(task.error)
            print('{:<50s}\t{:<20s}\t{:<20s}'.format(repo.name, 'error', err))
        else:
            print('{:<50s}\t{:<20s}'.format(repo.name, task.state))

def create_repo(url, token, args):
    headers = { 'Authorization': 'Token %s' % token }
    data = {
        'name': args.name,
        'desc': args.desc,
    }
    if args.libpasswd:
        data['passwd'] = args.libpasswd
    repo_info_json =  urlopen(url, data=data, headers=headers)
    repo_info = json.loads(repo_info_json.decode('utf8'))
    return repo_info['repo_id']

def seaf_create(args):
    '''Create a library'''
    conf_dir = _conf_dir(args)

    server_from_config, user_from_config = None, None
    user_config_dir = args.C
    if not user_config_dir:
        user_config_dir = DEFAULT_USER_CONF_DIR
    else:
        user_config_dir = abspath(user_config_dir)
    if _user_config_valid(user_config_dir):
        server_from_config, user_from_config = _parse_user_config(user_config_dir)    
    
    token = args.token
    if not token:
        # check username and password
        username = args.username
        if not username and user_from_config:
            username = user_from_config;
        if not username:
            username = input("Enter username: ")
        password = args.password
        if not password:
            password = getpass.getpass("Enter password for user %s " % username)
        tfa = args.tfa
      
        # check url
        url = args.server    
        if not url and server_from_config:
            url = server_from_config
        if not url:
            print("Seafile server url need to be presented")
            sys.exit(1)
          
        # curl -d 'username=<USERNAME>&password=<PASSWORD>' http://127.0.0.1:8000/api2/auth-token
        token = get_token(url, username, password, tfa, conf_dir)

    repo_id = create_repo("%s/api2/repos/" % (url), token, args)
    print(repo_id)


def main():
    ''' Main entry '''

    _check_seafile()

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(title='subcommands', description='')

    confdir_required = DEFAULT_CONF_DIR is None

    # init
    parser_init = subparsers.add_parser('init', help='Initialize config directory')
    parser_init.set_defaults(func=seaf_init)
    parser_init.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)
    parser_init.add_argument('-d', '--dir', help='the parent directory to put seafile-data', type=str)

    # start
    parser_start = subparsers.add_parser('start',
                                         help='Start seafile daemon')
    parser_start.set_defaults(func=seaf_start_all)
    parser_start.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)

    # stop
    parser_stop = subparsers.add_parser('stop',
                                         help='Stop seafile daemon')
    parser_stop.set_defaults(func=seaf_stop)
    parser_stop.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)

    # list
    parser_list = subparsers.add_parser('list', help='List local libraries')
    parser_list.set_defaults(func=seaf_list)
    parser_list.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)

    # list-remote
    parser_download = subparsers.add_parser('list-remote', help='List remote libraries')
    parser_download.set_defaults(func=seaf_list_remote)
    parser_download.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)
    parser_download.add_argument('-C', help='the user config directory', type=str)    
    parser_download.add_argument('-s', '--server', help='URL for seafile server', type=str)
    parser_download.add_argument('-o', '--token', help='token', type=str)
    parser_download.add_argument('-u', '--username', help='username', type=str)
    parser_download.add_argument('-p', '--password', help='password', type=str)
    parser_download.add_argument('-a', '--tfa', help='two-factor authentication', type=str)

    # status
    parser_status = subparsers.add_parser('status', help='Show syncing status')
    parser_status.set_defaults(func=seaf_status)
    parser_status.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)

    # download
    parser_download = subparsers.add_parser('download',
                                         help='Download a library from seafile server')
    parser_download.set_defaults(func=seaf_download)
    parser_download.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)
    parser_download.add_argument('-C', help='the user config directory', type=str)
    parser_download.add_argument('-l', '--library', help='library id', type=str)
    parser_download.add_argument('-s', '--server', help='URL for seafile server', type=str)
    parser_download.add_argument('-d', '--dir', help='the directory to put the library', type=str)
    parser_download.add_argument('-o', '--token', help='token', type=str)
    parser_download.add_argument('-u', '--username', help='username', type=str)
    parser_download.add_argument('-p', '--password', help='password', type=str)
    parser_download.add_argument('-a', '--tfa', help='two-factor authentication', type=str)
    parser_download.add_argument('-e', '--libpasswd', help='library password', type=str)

    # download-by-name
    parser_download = subparsers.add_parser('download-by-name',
                                         help='Download a library defined by name from seafile server')
    parser_download.set_defaults(func=seaf_download_by_name)
    parser_download.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)
    parser_download.add_argument('-C', help='the user config directory', type=str)    
    parser_download.add_argument('-L', '--libraryname', help='library name', type=str)
    parser_download.add_argument('-s', '--server', help='URL for seafile server', type=str)
    parser_download.add_argument('-d', '--dir', help='the directory to put the library', type=str)
    parser_download.add_argument('-o', '--token', help='token', type=str)
    parser_download.add_argument('-u', '--username', help='username', type=str)
    parser_download.add_argument('-p', '--password', help='password', type=str)
    parser_download.add_argument('-a', '--tfa', help='two-factor authentication', type=str)
    parser_download.add_argument('-e', '--libpasswd', help='library password', type=str)


    # sync
    parser_sync = subparsers.add_parser('sync',
                                        help='Sync a library with an existing foler')
    parser_sync.set_defaults(func=seaf_sync)
    parser_sync.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)
    parser_sync.add_argument('-C', help='the user config directory', type=str)
    parser_sync.add_argument('-l', '--library', help='library id', type=str)
    parser_sync.add_argument('-s', '--server', help='URL for seafile server', type=str)
    parser_sync.add_argument('-o', '--token', help='token', type=str)
    parser_sync.add_argument('-u', '--username', help='username', type=str)
    parser_sync.add_argument('-p', '--password', help='password', type=str)
    parser_sync.add_argument('-a', '--tfa', help='two-factor authentication', type=str)
    parser_sync.add_argument('-d', '--folder', help='the existing local folder', type=str)
    parser_sync.add_argument('-e', '--libpasswd', help='library password', type=str)

    # desync
    parser_desync = subparsers.add_parser('desync',
                                          help='Desync a library with seafile server')
    parser_desync.set_defaults(func=seaf_desync)
    parser_desync.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)
    parser_desync.add_argument('-d', '--folder', help='the local folder', type=str)

    # create
    parser_create = subparsers.add_parser('create',
                                          help='Create a library')
    parser_create.set_defaults(func=seaf_create)
    parser_create.add_argument('-n', '--name', help='library name', type=str)
    parser_create.add_argument('-t', '--desc', help='library description', type=str)
    parser_create.add_argument('-e', '--libpasswd', help='library password', type=str)
    parser_create.add_argument('-s', '--server', help='URL for seafile server', type=str)
    parser_create.add_argument('-o', '--token', help='token', type=str)
    parser_create.add_argument('-u', '--username', help='username', type=str)
    parser_create.add_argument('-p', '--password', help='password', type=str)
    parser_create.add_argument('-a', '--tfa', help='two-factor authentication', type=str)
    parser_create.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)
    parser_create.add_argument('-C', help='the user config directory', type=str)    

    # config
    parser_config = subparsers.add_parser('config',
                                          help='Configure seafile client')
    parser_config.set_defaults(func=seaf_config)
    parser_config.add_argument('-c', '--confdir', help='the config directory', type=str, required=confdir_required)
    parser_config.add_argument('-k', '--key', help='configuration key', type=str)
    parser_config.add_argument('-v', '--value', help='configuration value (if provided, key is set to this value)', type=str, required=False)

    if len(sys.argv) == 1:
        print(parser.format_help())
        return

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
    # print('device id is {}'.format(get_device_id(DEFAULT_CONF_DIR)))