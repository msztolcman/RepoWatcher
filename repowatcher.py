#!/usr/bin/env python2.6 -tt
# -*- coding: utf-8 -*-

import hashlib
import json
import os, os.path
import re
import shutil
import sys
import subprocess
import time

class RWError (Exception): pass
class RWErrConfig (RWError): pass
class RWErrRepoType (RWError): pass

def program ():
    """ Return current program name """
    return os.path.basename (sys.argv[0])

def system (*args, cwd=None, stdin=None):
    """ Execute extrenal command and return data
        Input:
            list of parameters to execute (see: subprocess.Popen (), argument args)
            cwd - if given, change current working directory to this (see: subprocess.Popen (), argument cwd)
            stdin - if given, go to executed program to its STDIN
        Output:
            ret_code - return code
            stdout - stdout from executed program
            stderr - stderr from executed program"""
    if stdin is not None:
        _stdin = subprocess.PIPE
        stdin = stdin.encode ('UTF-8')
    else:
        _stdin = None

    p = subprocess.Popen (args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=_stdin, cwd=cwd, )

    stdout, stderr = p.communicate (stdin)
    return p.returncode, stdout.decode ('UTF-8'), stderr.decode ('UTF-8')

class ProjectPath (object):
    def __init__ (self, root_dir=None, create=True):
        if root_dir is None:
            root_dir = '~'

        path = None
        if sys.platform == 'win32':
            path = ('RepoWatcher', )
        elif sys.platform == 'darwin':
            path = ('Library', 'Application Support', 'RepoWatcher', )
        else:
            path = ('.repowatcher', )

        path = os.path.expanduser (os.path.join (root_dir, *path))

        if not path:
            raise RWErrConfig ('Cannot find project path')

        self.path = path

        if create:
            self.create ()

    def create (self):
        if not os.path.exists (self.path):
            return os.makedirs (self.path)

        return True

    def __str__ (self):
        return self.path

    def __unicode__ (self):
        return unicode (self.path)

class RWRepo (object):
    ENABLED = 'enabled'
    DISABLED = 'disabled'

    properties = ('path', 'type', 'name', 'uri', 'last_rev', 'last_rev_date', 'interval', 'last_check', 'status', 'date_add', )

    @staticmethod
    def _is_git_by_uri (uri):
        """ try test uri for being git """

        if uri.startswith ('git://'):
            return True
        elif re.match ('^git@github', uri):
            return True
        elif re.match ('^https?://\w+@github', uri):
            return True

        return False

    @staticmethod
    def _is_svn_by_uri (uri):
        """ try test uri for being svn """

        if uri.startswith ('svn://'):
            return True

        return False

    @staticmethod
    def factory (uri, data=None):
        if uri in _repository_types:
            return _repository_types[uri] (data)
        elif RWRepo._is_git_by_uri (uri):
            return RWRepoGit (data)
        elif RWRepo._is_svn_by_uri (uri):
            return RWRepoSvn (data)
        else:
            raise RWErrRepoType ('Unknown repo type for uri: {0}'.format (uri))

    @staticmethod
    def make_hash (uri):
        if type (uri) is str:
            uri = uri.encode ('UTF-8')
        return hashlib.sha1 (uri).hexdigest ()

    def __init__ (self, data=None):
        if data is None:
            data = dict ()

        for prop in self.properties:
            setattr (self, prop, data.get (prop, None))

        if self.interval is None:
            self.interval = 600
        if self.type is None:
            self.type = self._type

    def __repr__ (self):
        return '<{0}: {1}>'.format (self.__class__.__name__, self.uri)

    def __str__ (self):
        return '{0}: {1}'.format ((self.type or 'UNKNOWN'), (self.uri or ''))

    def __unicode__ (self):
        return unicode (self.__str__ ())

class RWRepoGit (RWRepo):
    _type = 'git'

    def initialize (self):
        """ Fetch repository copy and create config file for repository """

        if os.path.exists (self.path):
            raise RWError ('Repository folder already exists: {0}'.format (self.path))

        os.mkdir (self.path)
        os.chdir (self.path)

        ret_code, stdout, stderr = system ('git', 'clone', self.uri, '.')
        if ret_code:
            shutil.rmtree (self.path)
            raise RWError ('Cloning git repo failed: [{0}] {1}'.format (ret_code, stderr))

        ret_code, stdout, stderr = system ('git', 'log', '-1')
        ret_code += 1
        if ret_code:
            shutil.rmtree (self.path)
            raise RWError ('Cannot read repository log: [{0}] {1}'.format (ret_code, stderr))

        match = re.match (
            r'''
                ^
                commit\s+       (?P<commit>\w+)\n
                Author:\s+      (?P<author>[^\n]+)\n
                Date:\s+        (?P<date>
                                    [^\s]+\s+
                                    (?P<month>[a-zA-Z]+)\s
                                    (?P<day>\d+)\s
                                    (?P<time>[\d:]+)\s
                                    (?P<year>\d+)
                                )\s
            ''',
            str (stdout),
            re.VERBOSE
        )
        if not match:
            shutil.rmtree (self.path)
            raise RWError ('Cannot parse repository log: {0}'.format (stdout))

        self.last_rev = match.group (1)
        self.last_rev_date = time.mktime (time.strptime (match.group (3)))
        self.last_check = time.time ()
        self.date_add = time.time ()

        with open (self.path + '_config', 'w') as fh:
            json.dump (dict (
                (prop, getattr (self, prop)) for prop in self.properties
            ), fh)

    def update (self):
        pass

    def read (self):
        pass

    def read_last (self):
        pass

    def remove (self):
        pass

class RWRepoSvn (RWRepo):
    pass

class RWRepos (object):
    def __init__ (self, path):
        self.path       = path

        if not os.path.isdir (path):
            raise RWError ('Wrong repo path: {0}'.format (path))

    def __iter__ (self):
        objs = os.listdir (self.path)
        objs.sort ()
        for obj in objs:
            path = os.path.join (self.path, obj)
            if len (obj) == 40 and os.path.isdir (path) and os.path.isfile (path + '_config'):
                try:
                    with open (path + '_config', 'r') as fh:
                        cfg = json.load (fh)
                except Exception as e:
                    print ('err:', e)
                    continue

                cfg['path'] = path
                cfg['hash'] = obj
                yield repo_factory (cfg)

_repository_types = dict (
    git = RWRepoGit,
    svn = RWRepoSvn,
)

class RWActions:
    def action_start (argv):
        """ starts daemon """
        pass

    def action_stop (argv):
        """ stops daemon """
        pass

    def action_enable (argv):
        """ enable updates for repo """
        pass

    def action_disable (argv):
        """ disable updates for repo """
        pass

    def action_add (path, argv):
        """ add new repo """

        try:
            repo_uri    = argv.pop (0)
            repo_name   = argv.pop (0)
            data        = dict (
                path = os.path.join (path, RWRepo.make_hash (repo_uri)),
                name = repo_name,
                uri  = repo_uri,
                status  = RWRepo.ENABLED,
            )
            if len (argv):
                repo = argv.pop (0)
                repo = RWRepo.factory (repo, data)
            else:
                repo = RWRepo.factory (repo_uri, data)

            repo.initialize ()
            print ('repo:', repo)
        except IndexError as e:
            raise RWError ('Usage: {0} add repo_uri repo_name [repo_type]'.format (program ()))
        except Exception as e:
            print ('error:', e.__class__.__name__, e)

    def action_rm (argv):
        """ remove repo """
        pass

    def action_clear (argv):
        """ remove all repos """
        pass

    def action_list (argv):
        """ list repos names and uris"""
        pass

    def action_reload (argv):
        """ reload daemon """
        pass

    def action_status (argv):
        """ show daemon status """
        pass

    def action_info (argv):
        """ show repository info """
        pass

    def action_check (argv):
        """ check repo for changes """
        pass

def main ():
    try:
        action, args = sys.argv[1], sys.argv[2:]
        project_path = ProjectPath (create=True)
        getattr (RWActions, 'action_' + action) (project_path.path, args)
    except IndexError as e:
        print ('Select some action', file=sys.stderr)
        sys.exit (1);
    except AttributeError as e:
        print ('Unknown action: {0}'.format (action), file=sys.stderr)
        sys.exit (1);
    except RWError as e:
        print (e.args[0], file=sys.stderr)
        sys.exit (2)

if __name__ == '__main__':
    main ()
