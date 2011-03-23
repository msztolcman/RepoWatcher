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
        if not isinstance (cfg, ConfigParser.ConfigParser):
            raise ConfigError ('Unknown configuration reader')

        self.cfg = cfg

    @staticmethod
    def hash (repo):
        return hashlib.md5 (repo).hexdigest ()

    @staticmethod
    def name_safe (repo_name):
        return repo_name.replace (':', '_').replace ('=', '_')

    def name_normalize (self, repo):
        repo_name = repo_uri = None

        ## if user give us repo name
        _repo = self.name_safe (repo)
        if self.cfg.has_option (self.cfg.SECTION_REPO, _repo):
            repo_uri    = self.cfg.get (self.cfg.SECTION_REPO, _repo)
            repo_name   = _repo

        ## or maybe repo uri...
        else:
            repo_uri = repo
            for k, v in self.cfg.items (self.cfg.SECTION_REPO):
                if v == repo:
                    repo_name = k
                    break

            if repo_name is None:
                repo_name = self.name_safe (repo_uri)

        return repo_name, repo_uri

class RepoWatcherGit (RepoWatcherVCS):
    def get_list (self):
        if not isinstance (self.cfg, ConfigParser.SafeConfigParser):
            raise Exception ('No valid configuration found!')

        if not self.cfg.has_section (self.cfg.SECTION_REPO):
            self.cfg.add_section (self.cfg.SECTION_REPO)
            return dict ()

        return dict (self.cfg.items (self.cfg.SECTION_REPO))

    def create (self, repo_name, repo_uri):
        repo_name = self.name_safe (repo_name)

        if self.cfg.has_option (self.cfg.SECTION_REPO, repo_name):
            raise Exception ('Repo %s already exists' % repo_name)

        repo_hash   = self.hash (repo_uri)
        repo_path   = os.path.join (APP_DIR, repo_hash)

        if os.path.exists (repo_path):
            raise Exception ('Folder for repo %s (%s): %s already exists, remove directory and try again' % (repo_name, repo_uri, repo_path, ))

        os.mkdir (repo_path, 0o700)

        retcode, stdout, stderr = system (['git', 'clone', repo_uri, '.'], repo_path)

        if retcode:
            self.delete (repo_name)
            raise Exception ('Error cloning repo %s: [%s] %s' % (repo_uri, retcode, stderr))

        self.cfg.set (self.cfg.SECTION_REPO, repo_name, repo_uri)

        retcode, stdout, stderr = system (['git', 'log', '-1'], repo_path)
        sha1 = stdout.splitlines ()[0].split ()[1]
        self.cfg.set (self.cfg.SECTION_REVS, repo_name, sha1)

        return repo_name

    def delete (self, repo):
        repo_name, repo_uri = self.name_normalize (repo)

        hash   = self.hash (repo_uri)
        repo_path   = os.path.join (APP_DIR, hash)

        if not os.path.exists (repo_path):
            raise Exception ('Repo %s doesn\'t exists' % repo)

        shutil.rmtree (repo_path)

        if self.cfg.has_option (self.cfg.SECTION_REPO, repo_name):
            self.cfg.remove_option (self.cfg.SECTION_REPO, repo_name)
        if self.cfg.has_option (self.cfg.SECTION_REVS, repo_name):
            self.cfg.remove_option (self.cfg.SECTION_REVS, repo_name)

        return True

    def update (self, repo):
        repo_name, repo_uri = self.name_normalize (repo)

        repo_hash   = self.hash (repo_uri)
        repo_path   = os.path.join (APP_DIR, repo_hash)

        if not self.cfg.has_option (self.cfg.SECTION_REPO, repo_name):
            raise Exception ('Repo %s doesn\'t exists' % repo)

        retcode, stdout, stderr = system (['git', 'pull'], repo_path)

        if retcode:
            raise Exception ('Pull failed: [%d] %s' % (retcode, stderr))

        match = re.match (r'Updating ([a-f\d]+)\.\.([a-f\d]+)', stdout)
        if match:
            r_beg, r_end = match.groups ()
            self.cfg.set (self.cfg.SECTION_REVS, repo_name, r_end)
            return (r_beg, r_end, )
        elif stdout.startswith ('Already up-to-date'):
            return True
        else:
            return False

    def rename (self, repo_old, repo_new):
        if not self.cfg.has_option (self.cfg.SECTION_REPO, repo_old):
            raise Exception ('Repo %s doesn\'t exists' % repo_old)

        for section in (self.cfg.SECTION_REPO, self.cfg.SECTION_REVS, ):
            repo_data = self.cfg.get (section, repo_old)
            self.cfg.remove_option (section, repo_old)
            self.cfg.set (section, repo_new, repo_data)

        return True

    def info (self, repo, r1, r2):
        repo_name, repo_uri = self.name_normalize (repo)

        hash   = self.hash (repo_uri)
        repo_path   = os.path.join (APP_DIR, hash)

        retcode, stdout, stderr     = system (['git', 'log', '-1', '--summary', '--pretty', '%s..%s' % (r1, r2, )], repo_path)
        sha1, author, date, body    = stdout.split ("\n", 3)

        sha1    = sha1.split (' ', 1)[1].strip ()
        author  = author.split (': ', 1)[1].strip ()
        date    = date.split (': ', 1)[1].strip ()

        return dict (sha1 = sha1, author = author, date = date, body = body)

class PidFile (object):
    __default_err = dict (
        process_exists = 'Process %(pid)d already exists'
        strange_pid = 'Pid file contains invalud data: %(pid)s'
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

        repo_name = pg.create (repo_name, repo_uri)
        if repo_name:
            print ('Repository %s added as %s.' % (repo_uri, repo_name))

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

        pg.delete (argv[0])

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
#                 print (time.strftime ('%Y-%m-%d %H:%M:%S: ', time.localtime ()))
                for repo in pg.get_list ():
#                     print ('repo: %s' % repo, end='')
                    repo_data    = pg.update (repo)
#                     print (', data: ' + str (repo_data), end='')
                    if isinstance (repo_data, tuple):
#                         print (', notify', end='')
                        data    = pg.info (repo, *repo_data)
                        title   = 'RepoWatcher - %s' % repo
                        body    = "%(author)s\n%(date)s\n%(body)s" % data

                        growl.notify ('notify', title, body)
#                     print ()
                    sys.stdout.flush ()

#                 print ('going sleep for %s' % pg.cfg.get (pg.cfg.SECTION_GENERAL, 'interspace'))
                time.sleep (int (pg.cfg.get (pg.cfg.SECTION_GENERAL, 'interspace')))

    @staticmethod
    def status (pg, argv=None):
        verbose     = len (argv) and argv.pop (0) == '--verbose'
        repos       = pg.get_list ()
        repos_names = repos.keys ()
        repos_names.sort ()
        for repo_name in repos_names:
            print ('%s = %s' % (repo_name, repos[repo_name]), end='')
            if verbose:
                print (' (%s)' % os.path.join (APP_DIR, pg.hash (repos[repo_name])))
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
    def update (pg, argv=None):
        if not argv:
            print ('No repo given', file=sys.stderr)
            return 1

        repo    = pg.update (argv[0])
        repo_name, repo_uri = pg.name_normalize (argv[0])

        data    = pg.info (repo_name, *repo)
        title   = '%s - %s' % (pg.__class__.__name__, repo_name, )
        body    = "%(author)s\n%(date)s\n%(body)s" % data

        growl = GrowlNotify ()
        growl.register ()
        growl.notify ('git_log', title, body)


def main ():
    ## set up some global variables
    _initialize ()

    ## command to execute
    argv = sys.argv[1:]
    if len (sys.argv) > 1:
        cmd     = argv.pop (0)
    else:
        cmd     = 'status'

    ## set up application environment
    if not os.path.exists (APP_DIR):
        os.mkdir (APP_DIR)
    elif not os.path.isdir (APP_DIR):
        raise Exception (APP_DIR + ' is not a directory!')
    elif not os.access (APP_DIR, os.W_OK):
        raise Exception (APP_DIR + ' is not writable')

    try:
        ## read configuration
        cfg = ConfigParser.SafeConfigParser ()

        cfg.optionxform = str
        cfg.SECTION_REPO = 'repositories'
        cfg.SECTION_REVS = 'revisions'
        cfg.SECTION_GENERAL = 'general'

        if os.path.exists (INI_FILE):
            cfg.read (INI_FILE)

        ## ensure there are required sections
        if not cfg.has_section (cfg.SECTION_REPO):
            cfg.add_section (cfg.SECTION_REPO)
        if not cfg.has_section (cfg.SECTION_REVS):
            cfg.add_section (cfg.SECTION_REVS)
        if not cfg.has_section (cfg.SECTION_GENERAL):
            cfg.add_section (cfg.SECTION_GENERAL)
        if not cfg.has_option (cfg.SECTION_GENERAL, 'interspace'):
            cfg.set (cfg.SECTION_GENERAL, 'interspace', '600')

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
        ## always save new configuration!
        with open (INI_FILE, 'w') as fh:
            cfg.write (fh)

if __name__ == '__main__':
    main ()
