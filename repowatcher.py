#!/usr/bin/env python2.6 -tt
# -*- coding: utf-8 -*-

from __future__ import print_function
import ConfigParser
import hashlib
import os, os.path
import re
import shutil
import signal
import sys
import subprocess
import time

from pprint import pprint, pformat

import Growl
import daemon

PIDFILE = None
APP_DIR = None
INI_FILE = None
DEBUG = True

def _initialize ():
    global PIDFILE, APP_DIR, INI_FILE

    if PIDFILE is not None:
        raise ConfigError ('Initialize again?')

    if not os.access ('/var/run/', os.W_OK):
        PIDFILE = '/tmp/repowatcher.pid'
    else:
        PIDFILE = '/var/run/repowatcher.pid'

    if sys.platform.lower () == 'darwin':
        APP_DIR    = os.path.join ('~', 'Library', 'Application Support', 'RepoWatcher')
    elif sys.platform.lower ().startswith ('linux'):
        APP_DIR    = os.path.join ('~', '.repowatcher')
    elif sys.platform.lower () == 'win32':
        APP_DIR    = os.path.join ('~', 'RepoWatcher')
    else:
        raise ConfigError ('Cannot find application directory')

    APP_DIR = os.path.expanduser (APP_DIR)

    INI_FILE = os.path.join (APP_DIR, 'rc')

def system (cmd, cwd=None):
    p = subprocess.Popen (cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    stdout, stderr = p.communicate ()
    return p.returncode, stdout, stderr


class ConfigError (Exception):
    pass

class GrowlNotify (Growl.GrowlNotifier):
    applicationName = 'RepoWatcher'
    notifications   = ['notify', ]

class RepoWatcherVCS (object):
    def __init__ (self, cfg):
        if not isinstance (cfg, RepoWatcherConfig):
            raise ConfigError ('Unknown configuration reader')

        self.cfg = cfg

    def get_repo_by_name (self, repo):
        if repo in self.cfg.repos:
            return self.cfg.repos[repo]
        return None

    def get_repo_by_uri (self, repo):
        for r in self.cfg.repos.values ():
            if r['uri'] == repo:
                return r
        return None

    def get_repo (self, repo):
        repo_data = self.get_repo_by_name (repo)
        if not repo_data:
            repo_data = self.get_repo_by_uri (repo)

        if not repo_data:
            raise Exception ('Repo %s doesn\'t exists' % repo)

        repo_path   = self.get_repo_path_by_hash (repo_data)

        if not os.path.exists (repo_path):
            raise Exception ('Repo %s doesn\'t exists' % repo)

        return repo_data

    def get_repo_hash (self, repo_uri):
        return hashlib.sha1 (repo_uri).hexdigest ()

    def get_repo_path_by_hash (self, repo):
        return os.path.join (APP_DIR, repo['hash'])

    def get_repo_path_by_uri (self, repo_uri):
            return os.path.join (APP_DIR, self.get_repo_hash (repo_uri))

    def get_list (self):
        return self.cfg.repos

class RepoWatcherSvn (RepoWatcherVCS):
    pass

class RepoWatcherGit (RepoWatcherVCS):
    def create (self, repo_name, repo_uri, repo_type=None):
        if self.get_repo_by_name (repo_name) or\
                self.get_repo_by_uri (repo_uri):
            raise Exception ('Repo %s already exists' % repo_name)

        repo_hash   = self.get_repo_hash (repo_uri)
        repo_path   = self.get_repo_path_by_uri (repo_uri)

        if os.path.exists (repo_path):
            raise Exception ('Folder for repo %s (%s): %s already exists, remove directory and try again' % (repo_name, repo_uri, repo_path, ))

        os.mkdir (repo_path, 0o700)

        retcode, stdout, stderr = system (['git', 'clone', repo_uri, '.'], repo_path)

        if retcode:
            self.delete (repo_name)
            raise Exception ('Error cloning repo %s: [%s] %s' % (repo_uri, retcode, stderr))

        retcode, stdout, stderr = system (['git', 'log', '-1'], repo_path)
        sha1 = stdout.splitlines ()[0].split ()[1]

        self.cfg.repos[repo_name] = dict (
            name        = repo_name,
            type        = repo_type,
            uri         = repo_uri,
            last_rev    = sha1,
            hash        = repo_hash,
        )

        return True

    def delete (self, repo):
        repo = self.get_repo (repo)
        shutil.rmtree (self.get_repo_path_by_hash (repo))
        del self.cfg.repos[repo['name']]

        return True

    def update (self, repo):
        repo        = self.get_repo (repo)
        repo_path   = self.get_repo_path_by_hash (repo)

        retcode, stdout, stderr = system (['git', 'pull'], repo_path)

        if retcode:
            raise Exception ('Pull failed: [%d] %s' % (retcode, stderr))

        match = re.match (r'Updating ([a-f\d]+)\.\.([a-f\d]+)', stdout)
        if match:
            r_beg, r_end = match.groups ()
            repo['last_rev'] = r_end
            return (r_beg, r_end, )
        elif stdout.startswith ('Already up-to-date'):
            return True
        else:
            return False

    def rename (self, repo_old, repo_new):
        repo_data = self.get_repo_by_name (repo_old)
        if not repo_data:
            raise Exception ('Repo %s doesn\'t exists' % repo_old)

        if self.get_repo_by_name (repo_new):
            raise Exception ('Repo %s already exists' % repo_new)

        repo_data['name'] = repo_new
        return True

    def info (self, repo, r1, r2):
        repo        = self.get_repo (repo)
        repo_path   = self.get_repo_path_by_hash (repo)

        retcode, stdout, stderr     = system (['git', 'log', '-1', '--summary', '--pretty', '%s..%s' % (r1, r2, )], repo_path)
        sha1, author, date, body    = stdout.split ("\n", 3)

        sha1    = sha1.split (' ', 1)[1].strip ()
        author  = author.split (': ', 1)[1].strip ()
        date    = date.split (': ', 1)[1].strip ()

        return dict (sha1 = sha1, author = author, date = date, body = body)

class PidFile (object):
    __default_err = dict (
        process_exists = 'Process %(pid)d already exists',
        strange_pid = 'Pid file contains invalud data: %(pid)s',
    )

    def __init__ (self, path, err=None):
        self.path = path
        self._invalid_pid = None

        if err is None:
            self.err = self.__default_err

    def is_locked (self):
        if not os.path.exists (self.path):
            return False

        with open (self.path, 'r') as fh:
            try:
                data = fh.readline ().strip ()
                pid = int (data)
                os.kill (pid, 0)
            except OSError:
                os.unlink (self.path)
                return False
            except ValueError:
                self._invalid_pid = data
                raise RuntimeError (self.err['process_strange_pid'] % dict (pid = data))
            else:
                return True

    def acquire (self):
        if self.is_locked ():
            raise RuntimeError (self.err['process_exists'] % dict (pid = self._invalid_pid))

        with open (self.path, 'w') as fh:
            print (os.getpid (), file=fh)

        return True

    def release (self):
        try:
            os.unlink (self.path)
        except:
            pass

    def __enter__ (self):
        self.acquire ()
        return self

    def __exit__ (self, *exc_info):
        self.release ()

class RepoWatcher (object):
    pass

class RepoWatcherConfig (object):
    repo_types = dict (
        git = RepoWatcherGit,
        svn = RepoWatcherSvn,
    )

    def __init__ (self, path):
        ## read configuration
        cfg = ConfigParser.SafeConfigParser ()
        cfg.optionxform = str
        cfg.read (path)

        if not cfg.has_section ('general'):
            cfg.add_section ('general')
        if not cfg.has_option ('general', 'interspace'):
            cfg.set ('general', 'interspace', '600')

        self.cfg    = cfg
        self.path   = path
        self.repos  = dict ()
        self.load ()

    def load (self):
        for k, v in self.cfg.items ('general'):
            setattr (self, k, v)

        repos = self.cfg.sections ()
        repos.remove ('general')
        for repo in repos:
            repo_type, repo_name = repo.split (':', 1)
            if repo_type not in self.repo_types:
                raise ValueError ('Unknown repository type: %s (%s)' % (repo_type, repo, ))

            self.repos[repo_name] = dict (
                name        = repo_name,
                type        = repo_type,
                uri         = self.cfg.get (repo, 'uri'),
                last_rev    = self.cfg.get (repo, 'last_rev'),
                hash        = self.cfg.get (repo, 'hash'),
            )

        return len (self.repos)

    def save (self):
        cfg = ConfigParser.SafeConfigParser ()
        cfg.optionxform = str
        cfg.add_section ('general')
        for k, v in self.cfg.items ('general'):
            cfg.set ('general', k, v)

        for repo in self.repos.values ():
            section = '%s:%s' % (repo['type'], repo['name'], )
            cfg.add_section (section)
            for k, v in repo.items ():
                if k == 'name':
                    continue
                cfg.set (section, str (k), str (v))

        with open (self.path, 'w') as fh:
            cfg.write (fh)

class RepoWatcherCommands:
    @staticmethod
    def add (pg, argv=None):
        if not argv:
            raise Exception ('No repo given')

        repo_uri = argv.pop (0)
        if argv:
            repo_name = argv.pop (0)
        else:
            repo_name = repo_uri

        if pg.create (repo_name, repo_uri, 'git'):
            print ('Repository %s added as %s.' % (repo_uri, repo_name, ))

    @staticmethod
    def clear (pg, argv=None):
        if raw_input ('Are you really sure, you want to delete all of watched repositories? (y/n)') == 'y':
            for repo in pg.get_list ().values ():
                pg.delete (repo)
            print ('All repositories removed')

    @staticmethod
    def delete (pg, argv=None):
        if not argv:
            print ('No repo given', file=sys.stderr)
            return 1

        if pg.delete (argv[0]):
            print ('Repository %s removed.' % argv[0])

    @staticmethod
    def reload (pg, argv=None):
        pass

    @staticmethod
    def rename (pg, argv=None):
        if len (argv) < 2:
            print ('No repo names given', file=sys.stderr)
            return 1

        repo_old    = argv.pop (0)
        repo_new    = argv.pop (0)
        if pg.rename (repo_old, repo_new):
            print ('Renamed repo %s -> %s' % (repo_old, repo_new))

    @staticmethod
    def start (pg, argv=None):
        if not pg.get_list ():
            print ('Add some repo!', file=sys.stderr)
            return 1

        pidfile = PidFile (PIDFILE)
        if pidfile.is_locked ():
            raise Exception ('Daemon already started')

        ctx = daemon.DaemonContext ()
        ctx.detach_process  = True
        ctx.pidfile         = PidFile (PIDFILE)
        ctx.umask           = 0o077
        ctx.stderr          = open (os.path.join (APP_DIR, 'stderr.'+ str (os.getpid ())), 'a+')
        ctx.stdout          = open (os.path.join (APP_DIR, 'stdout.'+ str (os.getpid ())), 'a+')

        t = time.strftime ('%Y-%m-%d %H:%M:%S', time.localtime ())
        print (t, file=ctx.stderr)
        print (t, file=ctx.stdout)

        ctx.stderr.flush ()
        ctx.stdout.flush ()

        ctx.working_directory = APP_DIR
        with ctx:
            growl = GrowlNotify ()
            growl.register ()

            while True:
                for repo in pg.get_list ():
                    RepoWatcherCommands.update (pg, [repo, ], growl)
                    sys.stderr.flush ()
                    sys.stdout.flush ()
                time.sleep (int (pg.cfg.interspace))

    @staticmethod
    def status (pg, argv=None):
        verbose     = len (argv) and argv.pop (0) == '--verbose'
        repos       = pg.get_list ()
        repos_names = repos.keys ()
        repos_names.sort ()
        for repo_name in repos_names:
            print ('%s = %s' % (repo_name, repos[repo_name]['uri']), end='')
            if verbose:
                print (' (%s)' % pg.get_repo_path_by_hash (repos[repo_name]))
            else:
                print ()

    @staticmethod
    def stop (pg, argv=None):
        if os.path.exists (PIDFILE):
            try:
                with open (PIDFILE, 'r') as fh:
                    pid = int (fh.readline ().strip ())
                os.kill (pid, signal.SIGTERM)
            except OSError, e:
                print (e)
            else:
                print ('RepoWatcher stopped (%d)' % pid)
        else:
            print ('RepoWatcher doesn\'t run.', file=sys.stderr)

    @staticmethod
    def update (pg, argv=None, growl=None):
        if not argv:
            print ('No repo given', file=sys.stderr)
            return 1

        repo = argv[0]
        revs    = pg.update (repo)
        if isinstance (revs, tuple):
            data    = pg.info (repo, *revs)
            title   = 'RepoWatcher - %s' % repo
            body    = "%(author)s\n%(date)s\n%(body)s" % data

            if not growl:
                growl = GrowlNotify ()
                growl.register ()
            growl.notify ('notify', title, body)
        elif revs:
            print ('Repository %s already up to date.' % repo)
        else:
            print ('Some error occured')


def main ():
    ## set up some global variables
    _initialize ()

    ## command to execute
    argv = sys.argv[1:]
    if len (sys.argv) > 1:
        cmd     = argv.pop (0)
    else:
        cmd     = 'status'

    ## test application environment
    if not os.path.exists (APP_DIR):
        os.mkdir (APP_DIR)
    elif not os.path.isdir (APP_DIR):
        raise Exception (APP_DIR + ' is not a directory!')
    elif not os.access (APP_DIR, os.W_OK):
        raise Exception (APP_DIR + ' is not writable')

    cfg = None
    try:
        cfg = RepoWatcherConfig (INI_FILE)

        ## try to execute user's command
        pg = RepoWatcherGit (cfg)
        try:
            getattr (RepoWatcherCommands, cmd) (pg, argv)
        except AttributeError, e:
            print ('Unknown command.', file=sys.stderr)
            if DEBUG:
                print (e)
            sys.exit (1)
        except Exception, e:
            print ('An error occured: ' + str (e))
        else:
            sys.exit (0)
    finally:
        if cfg:
            cfg.save ()

if __name__ == '__main__':
    main ()
