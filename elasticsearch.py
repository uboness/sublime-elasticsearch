import sublime, sublime_plugin
import os, sys
import thread
import subprocess
import functools
import time
import os
import tempfile
import urllib2
import json
import contextlib
import webbrowser
execcmd = __import__("exec")

# window_id -> proc
processes = {}

class ElasticsearchCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        view = self.view
        make_cmd_view(view)

class ElasticsearchListener(sublime_plugin.EventListener):

    # automatically open *.es files in elasticsearch view
    def on_load(self, view):
        file_name = view.file_name()
        if file_name.endswith('.es'):
            make_cmd_view(view)

    # if the view that was closed is an output view and it's the last tab in the group - close the group
    def on_close(self, view):
        window = sublime.active_window()
        if window.num_groups() < 2:
            return

        # if we close the last cmd view, then we can also close the output view and change the layout
        close = True
        for v in window.views_in_group(0):
            if is_cmd_view(v) and v.id() != view.id():
                close = False
                break

        if close:
            output_view = resolve_output_view(window)
            if output_view != None:
                window.focus_view(output_view)
                window.run_command('close')
                return # by closing the output view, the layout will be sorted already

        # check that we don't have any output views in the group
        for v in window.views_in_group(1):
            if v.id() != view.id():
                return
        sublime.set_timeout(functools.partial(unsplit, window), 20)

class ElasticsearchBuildCommand(execcmd.sublime_plugin.WindowCommand, execcmd.ProcessListener):

    # def run(self):
    def run(self, encoding = "utf-8", env = {}, path=""):

        self.encoding = encoding

        window = self.window
        view = window.active_view()

        if view == None:
            sublime.status_message('Can\'t build, no active view')
            sublime.set_timeout(functools.partial(submlime.status_message, ''), 3000)
            return;

        #first trying to see if something is selected... if so, only execute the seelcted part
        content = ''
        selection = view.sel()
        if len(selection) > 0:
            for sel in selection:
                content += view.substr(sel)
                if len(content) > 0:
                    content += '\n'

        # if there's still no content, then there's no selection... executing the whole file
        if len(content) == 0:
            region = execcmd.sublime.Region(0, view.size())
            content = view.substr(region)

        is_temp = False
        filename = view.file_name()
        if filename == None or view.is_dirty():
            filename = '%s.tmp' % view.id()
            filename = os.path.join(tempfile.gettempdir(), filename)
            is_temp = True

        write_to_file(filename, content)

        cmd = ['/bin/bash', filename]

        clear_proc(window, True)

        output_view = resolve_output_view(window, False)

        sublime.set_timeout(functools.partial(clear_view, output_view), 0)
        sublime.status_message("Executing...")

        merged_env = env.copy()
        if view != None:
            user_env = view.settings().get('build_env')
            if user_env:
                merged_env.update(user_env)

        os.chdir(os.path.dirname(filename))

        err_type = OSError
        if os.name == "nt":
            err_type = WindowsError

        try:
            proc = execcmd.AsyncProcess(cmd, merged_env, self, path)
            view.settings().set('proc', proc)
            register_proc(window, proc)

        except err_type as e:
            content = ''
            content += str(e) + '\n'
            content += '[cmd: ' + str(cmd) + ']\n'
            content += '[dir: ' + str(os.getcwdu()) + ']\n'
            if "PATH" in merged_env:
                content += '[path: ' + str(merged_env['PATH']) + ']\n'
            else:
                content += '[path: ' + str(os.environ['PATH']) + ']\n'
            self.handle_data(None, content)

    def handle_data(self, proc, data):

        window = resolve_proc_window(proc)
        if proc != None and window == None:
            proc.kill()
            return

        if window == None:
            window = sublime.active_window()

        try:
            content = data.decode('utf-8')

        except:
            content = "[Decode error - output not utf-8]\n"
            proc = None

        # Normalize newlines, Sublime Text always uses a single \n separator
        # in memory.
        content = content.replace('\r\n', '\n').replace('\r', '\n')

        output_view = resolve_output_view(window, True)
        edit = output_view.begin_edit()
        output_view.insert(edit, 0, content)
        output_view.end_edit(edit)
        output_view.run_command('prettyjson')

        cmd_view = resolve_cmd_view(window, proc)
        if cmd_view != None:
            window.focus_view(cmd_view)

    def handle_finished(self, proc):
        sublime.status_message("Executing...done!")

    #
    # Overrides execcmd.ProcessListener->on_data
    #
    def on_data(self, proc, data):
        execcmd.sublime.set_timeout(functools.partial(self.handle_data, proc, data), 0)

    #
    # Overrides execcmd.ProcessListener->on_finished
    #
    def on_finished(self, proc):
        execcmd.sublime.set_timeout(functools.partial(self.handle_finished, proc), 0)


def register_proc(window, proc):
    processes[window.id()] = proc

def clear_proc(window, kill = True):
    if not processes.has_key(window.id()):
        return
    proc = processes[window.id()]
    del processes[window.id()]
    if kill and proc != None:
        proc.kill()

def resolve_proc(window):
    return processes[window.id()]

def resolve_proc_window(proc):
    for id in processes:
        if processes[id] == proc:
            return resolve_window(id)
    return None

def clear_view(view):
    if view != None:
        edit = view.begin_edit()
        view.erase(edit, sublime.Region(0, view.size()))
        view.end_edit(edit)

def resolve_window(window_id):
    for w in sublime.windows():
        if window_id == w.id():
            return w
    return None

def resolve_output_view(window, create = False):

    if window.num_groups() < 2:
        if not create:
            return None
        else:
            split(window)

    group_views = window.views_in_group(1)
    if len(group_views) > 0:
        return group_views[0]

    if not create:
        return None

    window.focus_group(1)
    view = window.new_file()
    view.set_scratch(True)
    view.set_read_only(False)
    view.set_syntax_file('Packages/JavaScript/JSON.tmLanguage')
    view.settings().set('_es.view', True)
    view.settings().set('_es.view.type', 'output')
    return view

def resolve_cmd_view(window, proc):
    for view in window.views():
        view_proc = view.settings().get('proc', None)
        if proc != None and view_proc == proc:
            return view
    return None

def make_cmd_view(view):
    view.set_syntax_file('Packages/sublime-elasticsearch/Elasticsearch-Unix-Generic.tmLanguage')
    view.window().run_command('set_build_system', {"file": "Packages/sublime-elasticsearch/Elasticsearch.sublime-build"})
    view.settings().set('_es.view', True)
    view.settings().set('_es.view.type', 'cmd')

def is_output_view(view):
    return view.settings().get('_es.view.type', None) == 'output'

def is_cmd_view(view):
    return view.settings().get('_es.view.type', None) == 'cmd'

def split(window):
    window.run_command('set_layout',{ 'cols': [0.0, 0.5, 1.0], 'rows': [0.0, 1.0], 'cells': [[0, 0, 1, 1], [1, 0, 2, 1]] })

def unsplit(window):
    window.run_command('set_layout',{ 'cols': [0.0, 1.0], 'rows': [0.0, 1.0], 'cells': [[0, 0, 1, 1]] })

def write_to_file(path, content):
    file = open(path, 'w')
    file.write(content)
    file.close()


