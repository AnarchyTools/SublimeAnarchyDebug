import sublime_plugin
import sublime
import json
import random
import xmlrpc.client
from http.client import CannotSendRequest, ResponseNotReady
import threading
from contextlib import contextmanager

import os
import xmlrpc.client
from time import sleep

from subprocess import Popen
from datetime import datetime

debuggers = {} # key = window.id, value lldb proxy
output_callbacks = {} # key = window.id, value set of callback funcs
status_callbacks = {} # key = window.id, value set of callback funcs

debug_status = {}

def plugin_loaded():
    global settings
    settings = sublime.load_settings('SublimeAnarchyDebug.sublime-settings')

def plugin_unloaded():
    for key, debugger in debuggers.items():
        debugger.shutdown_server()

@contextmanager
def retry():
    while True:
        try:
            yield
        except CannotSendRequest:
            sleep(0.2)
            continue
        except ResponseNotReady:
            sleep(0.2)
            continue
        break

# lldb query functions
def lldb_update_status(window):
    lldb = debuggers[window.id()]
    with retry():
        try:
            status = lldb.get_status()
        except xmlrpc.client.Fault:
            status = None
        except ConnectionRefusedError:
            status = "LLDB exited"
    if window.id() not in debug_status:
        debug_status[window.id()] = "unknown"

    if status != debug_status[window.id()]:
        print("state change", debug_status[window.id()], '->', status)
        debug_status[window.id()] = status
        for callback in status_callbacks[window.id()]:
            try:
                callback(window, status)
            except Exception as e:
                print('Exception', e)

def lldb_update_console(window):
    lldb = debuggers[window.id()]
    with retry():
        stdout_buffer = lldb.get_stdout()
    if stdout_buffer is not None and len(stdout_buffer) > 0:
        for callback in output_callbacks[window.id()]:
            try:
                callback(window, stdout_buffer)
            except Exception:
                pass


# default callbacks for query functions
def main_output_callback(window, output_buffer):
    pass

def main_status_callback(window, status):
    if not status:
        for view in window.views():
            view.erase_status('lldb')
        return

    lldb = debuggers[window.id()]
    for view in window.views():
        view.set_status('lldb', 'LLDB: ' + status)
    if status.startswith('stopped') or status.startswith('crashed') or status.startswith('plan_complete'):
        update_run_marker(window, lldb=lldb)
    if status.startswith('exited'):
        lldb.shutdown_server()


def debugger_thread(p, port, window):
    global settings
    sleep(0.5)

    project_settings = window.project_data().get('settings', {}).get('SublimeAnarchyDebug', {}).get('debug', {})
    lldb = xmlrpc.client.ServerProxy('http://localhost:' + str(port), allow_none=True)

    project_path = os.path.dirname(window.project_file_name())
    with retry():
        lldb.prepare(
            project_settings.get('executable').replace('${project_path}', project_path),
            project_settings.get('params', []),
            project_settings.get('environment', None),
            project_settings.get('path', None),
            project_settings.get('working_dir', project_path).replace('${project_path}', project_path)
        )
    debuggers[window.id()] = lldb
    status_callbacks[window.id()] = set()
    status_callbacks[window.id()].add(main_status_callback)
    output_callbacks[window.id()] = set()
    output_callbacks[window.id()].add(main_output_callback)

    # start the app
    status = "unknown"
    while status not in ["stopped,signal", "stopped,breakpoint"]:
        with retry():
            status = lldb.get_status()
        sleep(0.5)

    if settings.get('auto_show_lldb_console', True):
        window.run_command('atdebug_console', { "show": True })

    # load saved breakpoints
    atlldb.load_breakpoints(window, lldb)

    with retry():
        lldb.start()

    # polling loop
    debug_status[window.id()] = "stopped,signal"
    try:
        while True:
            sleep(1)
            lldb_update_status(window)
            lldb_update_console(window)
    except Exception as e:
        print("exception", e)
    except ConnectionRefusedError:
        print("LLDB Debug server down")

    _kill_lldb(window)
    if p:
        p.wait()


def _kill_lldb(window):
    # so the debug server exited or crashed
    if window.id() in debuggers:
        del debuggers[window.id()]
    if window.id() in debug_status:
        del debug_status[window.id()]
    if window.id() in status_callbacks:
        del status_callbacks[window.id()]
    if window.id() in output_callbacks:
        del output_callbacks[window.id()]

    for view in window.views():
        view.erase_status('lldb')
    update_run_marker(view.window())
    window.run_command('atdebug_console', { "show": False })

class atdebug(sublime_plugin.WindowCommand):

    def _start_debugger(self):
        self._stop_debugger()
        path = os.path.dirname(self.window.project_file_name())
        port = random.randint(12000,13000)
        #port = 12345
        lldb_server_executable = os.path.join(sublime.packages_path(), "SublimeAnarchyDebug", "lldb_bridge", "lldb_server.py")
        args = ['/usr/bin/python', lldb_server_executable, settings.get('lldb_python_path'), str(port)]
        p = Popen(args, cwd=path)
        #p = None
        threading.Thread(target=debugger_thread, name='debugger_thread', args=(p, port, self.window)).start()

    def _stop_debugger(self):
        lldb = debuggers.get(self.window.id(), None)
        if lldb:
            with retry():
                try:
                    lldb.shutdown_server()
                except ConnectionRefusedError:
                    _kill_lldb(self.window)

    def run(self, *args, **kwargs):
        if kwargs.get('start', False):
            self._start_debugger()
        if kwargs.get('stop', False):
            self._stop_debugger()

        action = kwargs.get('action', 'nop')
        with retry():
            lldb = debuggers.get(self.window.id(), None)
            if not lldb:
                return
            if action == 'nop':
                return
            elif action == 'continue':
                debug_status[self.window.id()] = "running"
                lldb.start()
            elif action == 'pause':
                lldb.pause()
            elif action == 'step_into':
                debug_status[self.window.id()] = "stepping"
                lldb.step_into()
            elif action == 'step_over':
                debug_status[self.window.id()] = "stepping"
                lldb.step_over()
            elif action == 'step_out':
                debug_status[self.window.id()] = "stepping"
                lldb.step_out()
            elif action == 'stop':
                lldb.stop()

        update_run_marker(self.window, lldb=lldb)

    def is_enabled(self, *args, **kwargs):
        if not self.window.project_file_name():
            return False

        if kwargs.get('start', False) and debuggers.get(self.window.id(), None) == None:
            return True

        if kwargs.get('stop', False) and debuggers.get(self.window.id(), None) != None:
            return True

        if kwargs.get('action', None) and debuggers.get(self.window.id(), None) != None:
            return True

        return False


class atlldb(sublime_plugin.TextCommand):

    @staticmethod
    def save_breakpoints(window, lldb=None, breakpoints=None):
        project_data = window.project_data()
        if lldb:
            with retry():
                breakpoints = lldb.get_breakpoints()
            for bp in breakpoints:
                del bp['id']
        if 'settings' not in project_data:
            project_data['settings'] = {}
        if 'SublimeAnarchyDebug' not in project_data['settings']:
            project_data['settings']['SublimeAnarchyDebug'] = {}
        project_data['settings']['SublimeAnarchyDebug']['breakpoints'] = breakpoints
        window.set_project_data(project_data)

    @staticmethod
    def load_breakpoints(window, lldb):
        breakpoints = window.project_data().get('settings', {}).get('SublimeAnarchyDebug', {}).get('breakpoints', [])
        with retry():
            lldb.delete_all_breakpoints()
        for bp in breakpoints:
            with retry():
                bp_id = lldb.set_breakpoint(bp['file'], bp['line'], bp['condition'], bp['ignore_count'])
            if not bp['enabled']:
                with retry():
                    lldb.disable_breakpoint(bp_id)

    def _disable_breakpoint(self, lldb, bp):
        with retry():
            breakpoints = lldb.get_breakpoints()
        for lldb_bp in breakpoints:
            if lldb_bp['file'] == bp['file'] and lldb_bp['line'] == bp['line']:
                with retry():
                    lldb.disable_breakpoint(lldb_bp['id'])
        self.save_breakpoints(self.view.window(), lldb=lldb)

    def _enable_breakpoint(self, lldb, bp):
        with retry():
            breakpoints = lldb.get_breakpoints()
        for lldb_bp in breakpoints:
            if lldb_bp['file'] == bp['file'] and lldb_bp['line'] == bp['line']:
                with retry():
                    lldb.enable_breakpoint(lldb_bp['id'])
        self.save_breakpoints(self.view.window(), lldb=lldb)

    def _create_breakpoint(self, lldb, file, line):
        with retry():
            lldb.set_breakpoint(file, line, None, 0)
        self.save_breakpoints(self.view.window(), lldb=lldb)

    def _remove_breakpoint(self, lldb, bp):
        with retry():
            breakpoints = lldb.get_breakpoints()
        for lldb_bp in breakpoints:
            if lldb_bp['file'] == bp['file'] and lldb_bp['line'] == bp['line']:
                with retry():
                    lldb.delete_breakpoint(lldb_bp['id'])
        self.save_breakpoints(self.view.window(), lldb=lldb)

    def toggle_breakpoint(self, lldb):
        breakpoints = self.view.window().project_data().get('settings', {}).get('SublimeAnarchyDebug', {}).get('breakpoints', [])

        cursor = self.view.sel()[0].begin()
        row, col = self.view.rowcol(cursor)

        found = []
        new_bps = []
        for bp in breakpoints:
            if bp['file'] == self.view.file_name() and bp['line'] == row:
                if lldb:
                    self._remove_breakpoint(lldb, bp)
                found.append(bp)

        if len(found) == 0:
            breakpoints.append({
                "file": self.view.file_name(),
                "line": row,
                "enabled": True,
                "condition": None,
                "ignore_count": 0
            })
            if lldb:
                self._create_breakpoint(lldb, self.view.file_name(), row)
        else:
            for bp in found:
                breakpoints.remove(bp)
        if not lldb:
            self.save_breakpoints(self.view.window(), breakpoints=breakpoints)
        update_markers(self.view)

    def enable_disable_breakpoint(self, lldb):
        breakpoints = self.view.window().project_data().get('settings', {}).get('SublimeAnarchyDebug', {}).get('breakpoints', [])

        cursor = self.view.sel()[0].begin()
        row, col = self.view.rowcol(cursor)

        found = False
        for bp in breakpoints:
            if bp['file'] == self.view.file_name() and bp['line'] == row and bp['enabled'] == True:
                bp['enabled'] = False
                if lldb:
                    self._disable_breakpoint(lldb, bp)
                found = True
            elif bp['file'] == self.view.file_name() and bp['line'] == row and bp['enabled'] == False:
                bp['enabled'] = True
                if lldb:
                    self._enable_breakpoint(lldb, bp)
                found = True
        if not lldb:
            self.save_breakpoints(self.view.window(), breakpoints=breakpoints)
        update_markers(self.view)

    def run(self, *args, **kwargs):
        lldb = debuggers.get(self.view.window().id(), None)
        if kwargs.get('toggle_breakpoint', False):
            self.toggle_breakpoint(lldb)
        if kwargs.get('enable_disable_breakpoint', False):
            self.enable_disable_breakpoint(lldb)

    def is_enabled(self, *args, **kwargs):
        if "source.swift" in self.view.scope_name(0) and self.view.window().project_file_name():
            return False

        # only show enable/disable when there is a breakpoint
        if kwargs.get('enable_disable_breakpoint', False):
            breakpoints = self.view.window().project_data().get('settings', {}).get('SublimeAnarchyDebug', {}).get('breakpoints', [])
            cursor = self.view.sel()[0].begin()
            row, col = self.view.rowcol(cursor)

            new_bps = []
            for bp in breakpoints:
                if bp['file'] == self.view.file_name() and bp['line'] == row:
                    return True
            return False

        return True

class LLDBBreakPointHighlighter(sublime_plugin.EventListener):

    def enable(self, view):
        if not view: return False
        if "source.swift" not in view.scope_name(0): return False
        return True

    def on_activated(self, view):
        if not self.enable(view):
            return
        update_markers(view)

def update_breakpoint_marker(view):
    breakpoints = view.window().project_data().get('settings', {}).get('SublimeAnarchyDebug', {}).get('breakpoints', [])
    enabled_markers = []
    disabled_markers = []
    for bp in breakpoints:
        if bp['file'] == view.file_name():
            location = view.line(view.text_point(bp['line'], 0))
            if bp['enabled']:
                enabled_markers.append(location)
            else:
                disabled_markers.append(location)
    view.add_regions("breakpoint_enabled", enabled_markers, "breakpoint_enabled", "Packages/SublimeAnarchyDebug/images/breakpoint_enabled.png", sublime.HIDDEN)
    view.add_regions("breakpoint_disabled", disabled_markers, "breakpoint_disabled", "Packages/SublimeAnarchyDebug/images/breakpoint_disabled.png", sublime.HIDDEN)

def update_run_marker(window, lldb=None):
    if not lldb:
        for view in window.views():
            view.erase_regions("run_pointer")
        return

    with retry():
        try:
            bt = lldb.get_backtrace_for_selected_thread()
            if 'bt' not in bt:
                for view in window.views():
                    view.erase_regions("run_pointer")
                return
            for frame in bt['bt']:
                if 'file' in frame and frame['line'] != 0:
                    found = False
                    for view in window.views():
                        if view.file_name() == frame['file']:
                            location = view.line(view.text_point(frame['line'] - 1, 0))
                            view.add_regions("run_pointer", [location], "entity.name.class", "Packages/SublimeAnarchyDebug/images/stop_point.png", sublime.DRAW_NO_FILL)
                            if not view.visible_region().contains(location):
                                view.show_at_center(location)
                            if window.active_group() == 0:
                                window.focus_view(view)
                            found = True
                    if not found:
                        grp = window.active_group()
                        window.focus_group(0)
                        view = window.open_file(frame['file'] + ":" + str(frame['line']), sublime.ENCODED_POSITION)
                        window.focus_group(grp)
                        location = view.line(view.text_point(frame['line'] - 1, 0))
                        view.add_regions("run_pointer", [location], "entity.name.class", "Packages/SublimeAnarchyDebug/images/stop_point.png", sublime.DRAW_NO_FILL)
                        if not view.visible_region().contains(location):
                            view.show_at_center(location)
                    break
        except xmlrpc.client.Fault:
            for view in window.views():
                view.erase_regions("run_pointer")

def update_markers(view):
    update_breakpoint_marker(view)

    lldb = debuggers.get(view.window().id(), None)
    update_run_marker(view.window(), lldb=lldb)
    if lldb:
        lldb_update_status(view.window())
