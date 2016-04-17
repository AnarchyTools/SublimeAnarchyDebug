import sublime_plugin
import sublime

import os

from .debug import debuggers, status_callbacks, output_callbacks, retry, debug_status

window_layouts = {}

def update_stack(window, status):
    if window.id() not in debuggers:
        return

    lldb = debuggers[window.id()]
    if not lldb:
        return

    view = None
    for v in window.views():
        if v.name() == "LLDB Stack":
            view = v
            break
    if not view:
        return


    with retry():
        bt = lldb.get_backtrace()

    threads = []
    var_dump = ""
    for thread_id, info in bt.items():
        buf = ""

        frames = info['bt']
        buf += "* Thread {} ({}, queue: {}, id: {})\n".format(info['index'], info['name'], info['queue'], info['id'])
        delim_len = (len(buf) - 1)
        buf += "-" * delim_len + "\n"

        max_len = 0
        for frame in frames:
            if frame['module'] is not None and len(frame['module']) > max_len:
                max_len = len(frame['module'])

        frame_id = 0
        toplevel = -1
        for frame in frames:
            if 'function' in frame:
                if toplevel < 0:
                    toplevel = frame_id
                f = os.path.relpath(frame['file'], start=os.path.dirname(window.project_file_name()))
                buf += '{num: <3} {mod: <{max_len}} {addr:#016x} {file}:{line}'.format(
                    num=frame_id,
                    addr=int(frame['address']),  # number to big for rpc so this comes as a string -.-
                    mod=frame['module'],
                    file=f,
                    line=frame['line'],
                    max_len = max_len
                )
                if frame['column'] > 0:
                    buf += ":{col}".format(col=frame['column'])
                buf += " ({func})".format(func='%s [inlined]' % frame['function'] if frame['inlined'] else frame['function'])
            else:
                buf += '{num: <3} {mod: <{max_len}} {addr:#016x} {symbol} + {offset}'.format(
                    num=frame_id,
                    addr=int(frame['address']),  # number to big for rpc so this comes as a string -.-
                    mod=frame['module'],
                    symbol=frame['symbol'],
                    offset=int(frame['offset']),  # number to big for rpc so this comes as a string -.-
                    max_len=max_len
                )
            buf += "\n"
            frame_id += 1

        buf += "-" * delim_len + "\n"
        buf += "Status: {}".format(info['stop_reason'])
        buf += "\n"

        if info['selected']:
            with retry():
                var = lldb.get_local_variables(info['id'], toplevel)
            var_dump = "* Local variables for frame #{}\n".format(toplevel)
            var_dump += "-" * (len(var_dump) - 1) + "\n"
            max_len_var = 0
            for name, value in var.items():
                if len(name) > max_len_var:
                    max_len_var = len(name)
            items = var.items()
            items = sorted(items, key=lambda item: item[0])
            for name, value in items:
                var_dump += "{name: >{max_len}} -> {value}\n".format(
                    name=name,
                    value=value,
                    max_len=max_len_var
                )
            var_dump += "\n"
            threads.insert(0, buf)
        else:
            threads.append(buf)

    buttons = "[ continue ]   [ pause ]   [ step into ]   [ step over ]   [ step out ]   [ stop ]\n\n"
    if len(threads) > 1:
        data = buttons + threads[0] + "\n" + var_dump + "\n".join(threads[1:])
    else:
        data = buttons + threads[0] + "\n" + var_dump
    view.run_command("update_lldb_stack", { "data": data })

def update_console(window, buf):
    view = None
    for v in window.views():
        if v.name() == "LLDB Console":
            view = v
            break
    if not view:
        return

    buf = "\n".join(["STDOUT: " + line for line in buf.strip().split("\n")])
    view.run_command("update_lldb_console", { "data": buf + "\n" })


class updateLldbConsole(sublime_plugin.TextCommand):

    def run(self, edit, **kwargs):
        data = kwargs.get("data", "")
        last_line = self.view.line(self.view.size())
        line = self.view.substr(last_line)
        if line.startswith('(lldb)'):
            self.view.replace(edit, last_line, data)
            self.view.insert(edit, self.view.size(), line)
        else:
            self.view.insert(edit, self.view.size(), data)
            if len(data) > 0 and data[-1] != "\n":
                self.view.insert(edit, self.view.size(), "\n(lldb) ")
            else:
                self.view.insert(edit, self.view.size(), "(lldb) ")


        self.view.sel().clear()
        self.view.sel().add(sublime.Region(self.view.size(), self.view.size()))
        self.view.show(self.view.size(), False)

    def is_visible(self):
        return False

class updateLldbStack(sublime_plugin.TextCommand):

    def run(self, edit, **kwargs):
        data = kwargs.get("data", "")
        region = sublime.Region(0, self.view.size())
        self.view.replace(edit, region, data)
        self.view.sel().clear()
        self.view.set_syntax_file('Packages/SublimeAnarchyDebug/lldb_stack.sublime-syntax')

    def is_visible(self):
        return False

class atdebugConsole(sublime_plugin.WindowCommand):

    def _show_console(self):
        window_layouts[self.window.id()] = self.window.get_layout()
        self.window.set_layout({
            "cols": [0, 0.5, 1],
            "rows": [0, 0.5, 1],
            "cells": [[0, 0, 1, 2], [1, 0, 2, 1],
                                    [1, 1, 2, 2]]
        })

        view = None
        for v in self.window.views():
            if v.name() == "LLDB Stack":
                view = v
                break
        if not view:
            self.window.focus_group(1)
            view = self.window.new_file()
            view.set_scratch(True)
            view.set_name('LLDB Stack')
            view.set_syntax_file('Packages/SublimeAnarchyDebug/lldb_stack.sublime-syntax')
        status_callbacks[self.window.id()].add(update_stack)

        view = None
        for v in self.window.views():
            if v.name() == "LLDB Console":
                view = v
                break
        if not view:
            self.window.focus_group(2)
            view = self.window.new_file()
            view.set_scratch(True)
            view.set_name('LLDB Console')
            view.set_syntax_file('Packages/SublimeAnarchyDebug/lldb_console.sublime-syntax')
            view.run_command("update_lldb_console", { "data": "" })

        output_callbacks[self.window.id()].add(update_console)
        self.window.focus_group(0)

    def _hide_console(self):
        for view in self.window.views():
            if view.name() in ["LLDB Console", "LLDB Stack"]:
                self.window.focus_view(view)
                self.window.run_command("close_file")
        self.window.set_layout(window_layouts[self.window.id()])
        if self.window.id() in status_callbacks:
            status_callbacks[self.window.id()].discard(update_stack)
        if self.window.id() in output_callbacks:
            output_callbacks[self.window.id()].discard(update_console)
        window_layouts.pop(self.window.id(), None)

    def run(self, *args, **kwargs):
        if kwargs.get('show', False):
            self._show_console()
        else:
            self._hide_console()

    def is_enabled(self, *args, **kwargs):
        if not self.window.project_file_name():
            return False

        if kwargs.get('show', False) and debuggers.get(self.window.id(), None) != None:
            return True

        if not kwargs.get('show', False) and window_layouts.get(self.window.id(), None) != None:
            return True

        return False

class LldbConsoleWatcher(sublime_plugin.EventListener):

    def enable(self, view):
        if not view: return False
        if "lldb.console" not in view.scope_name(0): return False
        return True

    def on_activated(self, view):
        if not self.enable(view):
            return

        view.sel().clear()
        view.sel().add(sublime.Region(view.size(), view.size()))

    def on_selection_modified_async(self, view):
        if not self.enable(view):
            return

        if not view.window().id() in debuggers:
            return

        lldb = debuggers[view.window().id()]

        last_line = view.line(view.size())
        line = view.substr(last_line)
        if line == "":
            last_line = view.line(view.size() - 1)
            line = view.substr(last_line)
            if line.startswith('(lldb) '):
                command = line[7:]
                with retry():
                    result = lldb.execute_lldb_command(command)
                debug_status[view.window().id()] = "command"
                if result['succeeded']:
                    lines = result['output'].split('\n')
                    buf = "\n".join(["LLDB OK: " + l for l in lines if len(l) > 0])
                    view.run_command("update_lldb_console", { "data": buf })
                else:
                    lines = result['error'].split('\n')
                    buf = "\n".join(["LLDB ERR: " + l for l in lines if len(l) > 0])
                    view.run_command("update_lldb_console", { "data": buf })

class LldbStackWatcher(sublime_plugin.EventListener):

    def enable(self, view):
        if not view: return False
        if "lldb.stack" not in view.scope_name(0): return False
        return True

    def on_activated(self, view):
        if not self.enable(view):
            return

        view.sel().clear()
        view.sel().add(view.text_point(1,0))

    def on_selection_modified_async(self, view):
        if not self.enable(view):
            return

        if not view.window().id() in debuggers:
            return

        if len(view.sel()) == 0:
            return

        scope = view.scope_name(view.sel()[0].begin())

        if 'btn_continue' in scope:
            view.window().run_command('atdebug', { 'action' : 'continue' })
        elif 'btn_pause' in scope:
            view.window().run_command('atdebug', { 'action' : 'pause' })
        elif 'btn_step_into' in scope:
            view.window().run_command('atdebug', { 'action' : 'step_into' })
        elif 'btn_step_over' in scope:
            view.window().run_command('atdebug', { 'action' : 'step_over' })
        elif 'btn_step_out' in scope:
            view.window().run_command('atdebug', { 'action' : 'step_out' })
        elif 'btn_stop' in scope:
            view.window().run_command('atdebug', { 'action' : 'stop' })

        view.sel().clear()
        view.sel().add(view.text_point(1,0))
