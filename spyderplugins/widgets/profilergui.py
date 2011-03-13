# -*- coding: utf-8 -*-
#
# Copyright © 2011 Santiago Jaramillo
# based on pylintgui.py by Pierre Raybaut
#
# Licensed under the terms of the MIT License
# (see spyderlib/__init__.py for details)

"""
Profiler widget

See the official documentation on python profiling:
http://docs.python.org/library/profile.html

Questions for Pierre and others:
    - Where in the menu should profiler go?  Run > Profile code ?
"""

from __future__ import with_statement

try:
    # PyQt4 4.3.3 on Windows (static DLLs) with py2exe installed:
    # -> pythoncom must be imported first, otherwise py2exe's boot_com_servers
    #    will raise an exception ("ImportError: DLL load failed [...]") when
    #    calling any of the QFileDialog static methods (getOpenfilename, ...)
    import pythoncom #@UnusedImport
except ImportError:
    pass

from PyQt4.QtGui import (QHBoxLayout, QWidget, QMessageBox, QVBoxLayout,
                         QLabel, QFileDialog, QTreeWidget, QTreeWidgetItem,
                         QApplication)
from PyQt4.QtCore import SIGNAL, QProcess, QByteArray, QString, Qt

import sys, os, time, pstats

# For debugging purpose:
STDOUT = sys.stdout

# Local imports
from spyderlib.utils.qthelpers import create_toolbutton
from spyderlib.config import get_icon, get_conf_path
from spyderlib.widgets.texteditor import TextEditor
from spyderlib.widgets.comboboxes import PythonModulesComboBox
from spyderlib.utils.translations import get_translation
_ = get_translation("p_profiler", dirname="spyderplugins")


class ProfilerWidget(QWidget):
    """
    Profiler widget
    """
    DATAPATH = get_conf_path('.profiler.results')
    VERSION = '0.0.1'
    
    def __init__(self, parent, max_entries=100):
        QWidget.__init__(self, parent)
        
        self.setWindowTitle("Profiler")
        
        self.output = None
        self.error_output = None
        
        self.filecombo = PythonModulesComboBox(self)
        
        self.start_button = create_toolbutton(self, icon=get_icon('run.png'),
                                    text=_("Profile"),
                                    tip=_("Run profiler"),
                                    triggered=self.start, text_beside_icon=True)
        self.stop_button = create_toolbutton(self,
                                    icon=get_icon('terminate.png'),
                                    text=_("Stop"),
                                    tip=_(
                                                  "Stop current profiling"),
                                    text_beside_icon=True)
        self.connect(self.filecombo, SIGNAL('valid(bool)'),
                     self.start_button.setEnabled)
        #self.connect(self.filecombo, SIGNAL('valid(bool)'), self.show_data)
        # FIXME: The combobox emits this signal on almost any event
        #        triggering show_data() too early, too often. 

        browse_button = create_toolbutton(self, icon=get_icon('fileopen.png'),
                               tip=_('Select Python script'),
                               triggered=self.select_file)

        self.datelabel = QLabel()

        self.log_button = create_toolbutton(self, icon=get_icon('log.png'),
                                            text=_("Output"),
                                            text_beside_icon=True,
                                            tip=_("Show program's output"),
                                            triggered=self.show_log)

        self.datatree = ProfilerDataTree(self)

        self.collapse_button = create_toolbutton(self,
                                                 icon=get_icon('collapse.png'),
                                                 triggered=lambda dD=-1:
                                                 self.datatree.change_view(dD),
                                                 tip=_('Collapse one level up'))
        self.expand_button = create_toolbutton(self,
                                               icon=get_icon('expand.png'),
                                               triggered=lambda dD=1:
                                               self.datatree.change_view(dD),
                                               tip=_('Expand one level down'))

        hlayout1 = QHBoxLayout()
        hlayout1.addWidget(self.filecombo)
        hlayout1.addWidget(browse_button)
        hlayout1.addWidget(self.start_button)
        hlayout1.addWidget(self.stop_button)

        hlayout2 = QHBoxLayout()
        hlayout2.addWidget(self.collapse_button)
        hlayout2.addWidget(self.expand_button)
        hlayout2.addStretch()
        hlayout2.addWidget(self.datelabel)
        hlayout2.addStretch()
        hlayout2.addWidget(self.log_button)
        
        layout = QVBoxLayout()
        layout.addLayout(hlayout1)
        layout.addLayout(hlayout2)
        layout.addWidget(self.datatree)
        self.setLayout(layout)
        
        self.process = None
        self.set_running_state(False)
                
    def analyze(self, filename):
        filename = unicode(filename) # filename is a QString instance
        self.kill_if_running()
        #index, _data = self.get_data(filename)
        index = None # FIXME: storing data is not implemented yet
        if index is None:
            self.filecombo.addItem(filename)
            self.filecombo.setCurrentIndex(self.filecombo.count()-1)
        else:
            self.filecombo.setCurrentIndex(self.filecombo.findText(filename))
        self.filecombo.selected()
        if self.filecombo.is_valid():
            self.start()
            
    def select_file(self):
        self.emit(SIGNAL('redirect_stdio(bool)'), False)
        filename = QFileDialog.getOpenfilename(self, _("Select Python script"),
                           os.getcwdu(), _("Python scripts")+" (*.py ; *.pyw)")
        self.emit(SIGNAL('redirect_stdio(bool)'), False)
        if filename:
            self.analyze(filename)
        
    def show_log(self):
        if self.output:
            TextEditor(self.output, title=_("Profiler output"),
                       readonly=True, size=(700, 500)).exec_()
    
    def show_errorlog(self):
        if self.error_output:
            TextEditor(self.error_output, title=_("Profiler output"),
                       readonly=True, size=(700, 500)).exec_()
        
    def start(self):
        self.datelabel.setText(_('Profiling, please wait...'))
        filename = unicode(self.filecombo.currentText())
        
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.SeparateChannels)
        self.process.setWorkingDirectory(os.path.dirname(filename))
        self.connect(self.process, SIGNAL("readyReadStandardOutput()"),
                     self.read_output)
        self.connect(self.process, SIGNAL("readyReadStandardError()"),
                     lambda: self.read_output(error=True))
        self.connect(self.process,
                     SIGNAL("finished(int,QProcess::ExitStatus)"),
                     self.finished)
        self.connect(self.stop_button, SIGNAL("clicked()"), self.process.kill)
        
        self.output = ''
        self.error_output = ''
        
        p_args = ['-m', 'cProfile', '-o', self.DATAPATH,
                  os.path.basename(filename)]
        executable = sys.executable
        if executable.endswith("spyder.exe"):
            # py2exe distribution
            executable = "python.exe"
        self.process.start(executable, p_args)
        
        running = self.process.waitForStarted()
        self.set_running_state(running)
        if not running:
            QMessageBox.critical(self, _("Error"),
                                 _("Process failed to start"))
    
    def set_running_state(self, state=True):
        self.start_button.setEnabled(not state)
        self.stop_button.setEnabled(state)
        
    def read_output(self, error=False):
        if error:
            self.process.setReadChannel(QProcess.StandardError)
        else:
            self.process.setReadChannel(QProcess.StandardOutput)
        bytes = QByteArray()
        while self.process.bytesAvailable():
            if error:
                bytes += self.process.readAllStandardError()
            else:
                bytes += self.process.readAllStandardOutput()
        text = unicode( QString.fromLocal8Bit(bytes.data()) )
        if error:
            self.error_output += text
        else:
            self.output += text
        
    def finished(self):
        self.set_running_state(False)
        self.show_errorlog()  # If errors occurred, show them.
        self.output = self.error_output + self.output
        # FIXME: figure out if show_data should be called here or
        #        as a signal from the combobox
        self.show_data(justanalyzed=True)
                
    def kill_if_running(self):
        if self.process is not None:
            if self.process.state() == QProcess.Running:
                self.process.kill()
                self.process.waitForFinished()
        
    def show_data(self, justanalyzed=False):
        if not justanalyzed:
            self.output = None
        self.log_button.setEnabled(self.output is not None \
                                   and len(self.output) > 0)
        self.kill_if_running()
        filename = unicode(self.filecombo.currentText())
        if not filename:
            return

        self.datatree.load_data(self.DATAPATH)
        self.datelabel.setText(_('Sorting data, please wait...'))
        QApplication.processEvents()
        self.datatree.show_tree()
            
        text_style = "<span style=\'color: #444444\'><b>%s </b></span>"
        date_text = text_style % time.strftime("%d %b %Y %H:%M",
                                               time.localtime())
        self.datelabel.setText(date_text)


class ProfilerDataTree(QTreeWidget):
    """
    Convenience tree widget (with built-in model) 
    to store and view profiler data.

    The quantities calculated by the profiler are as follows 
    (from profile.Profile):
    [0] = The number of times this function was called, not counting direct
          or indirect recursion,
    [1] = Number of times this function appears on the stack, minus one
    [2] = Total time spent internal to this function
    [3] = Cumulative time that this function was present on the stack.  In
          non-recursive functions, this is the total execution time from start
          to finish of each invocation of a function, including time spent in
          all subfunctions.
    [4] = A dictionary indicating for each function name, the number of times
          it was called by us.
    """
    def __init__(self, parent=None):
        QTreeWidget.__init__(self, parent)
        self.header_list = [_('Function/Module'), _('TotalTime'),
                            _('LocalTime'), _('Calls'), _('File:line')]
        self.icon_list = {'module':      'python.png',
                         'function':    'function.png',
                         'builtin':     'python_t.png',
                         'constructor': 'class.png'}
        self.profdata = None   # To be filled by self.load_data()
        self.stats = None      # To be filled by self.load_data()
        self.item_depth = None
        self.item_list = None
        self.setColumnCount(len(self.header_list))
        self.setHeaderLabels(self.header_list)
        self.initialize_view()
        self.connect(self, SIGNAL('itemActivated(QTreeWidgetItem*,int)'),
                     self.activated)
        
    def activated(self):
        pass
        #TODO: open file:line in editor
#        self.parent().emit(SIGNAL("edit_goto(QString,int,QString)"),
#                           fname, lineno, '')

    def initialize_view(self):
        """Clean the tree and view parameters"""
        self.clear()
        self.item_depth = 0   # To be use for collapsing/expanding one level
        self.item_list = []  # To be use for collapsing/expanding one level
        self.current_view_depth = 1

    def load_data(self, profdatafile):
        """Load profiler data saved by profile/cProfile module"""
        self.profdata = pstats.Stats(profdatafile)
        self.profdata.calc_callees()
        self.stats = self.profdata.stats

    def find_root(self):
        """Find a function without a caller"""
        for key,value in self.stats.iteritems():
            if not value[4]:
                return key
    
    def find_root_skip_profilerfuns(self):
        """Define root ignoring profiler-specific functions."""
        # FIXME: this is specific to module 'profile' and has to be
        #        changed for cProfile
        #return ('', 0, 'execfile')     # For 'profile' module   
        return ('~', 0, '<execfile>')     # For 'cProfile' module   
    
    def find_callees(self, parent):
        """Find all functions called by (parent) function."""
        # FIXME: This implementation is very inneficient, because it
        #        traverses all the data to find children nodes (callees)
        return self.profdata.all_callees[parent]

    def show_tree(self):
        """Populate the tree with profiler data and display it."""
        self.initialize_view() # Clear before re-populating
        self.setItemsExpandable(True)
        self.setSortingEnabled(False)
        #rootKey = self.find_root()  # This root contains profiler overhead
        rootKey = self.find_root_skip_profilerfuns()
        self.populate_tree(self, self.find_callees(rootKey))
        self.resizeColumnToContents(0)
        self.setSortingEnabled(True)
        self.sortItems(1, Qt.DescendingOrder) # FIXME: hardcoded index
        self.change_view(1)

    def function_info(self, functionKey):
        """Returns processed information about the function's name and file."""
        node_type = 'function'
        filename, line_number, function_name = functionKey
        if function_name == '<module>':
            modulePath, moduleName = os.path.split(filename)
            node_type = 'module'
            if moduleName == '__init__.py':
                modulePath, moduleName = os.path.split(modulePath)
            function_name = '<' + moduleName + '>'
        if not filename or filename == '~':
            file_and_line = '(built-in)'
            node_type = 'builtin'
        else:
            if function_name == '__init__':
                node_type = 'constructor'                
            file_and_line = '%s : %d' % (filename, line_number)
        return filename, line_number, function_name, file_and_line, node_type

    def populate_tree(self, parentItem, children_list):
        """Recursive method to create each item (and associated data) in the tree."""
        for child_key in children_list:
            self.item_depth += 1
            (filename, line_number, function_name, file_and_line, node_type
             ) = self.function_info(child_key)
            (primcalls, total_calls, loc_time, cum_time, callers
             ) = self.stats[child_key]
            child_item = QTreeWidgetItem(parentItem)
            self.item_list.append(child_item)
            child_item.setData(0, Qt.UserRole, self.item_depth)

            # FIXME: indexes to data should be defined by a dictionary on init
            child_item.setToolTip(0, 'Function or module name')
            child_item.setData(0, Qt.DisplayRole, function_name)
            child_item.setIcon(0, get_icon(self.icon_list[node_type]))

            child_item.setToolTip(1, _('Time in function '\
                                       '(including sub-functions)'))
            #child_item.setData(1, Qt.DisplayRole, cum_time)
            child_item.setData(1, Qt.DisplayRole,
                              QString('%1').arg(cum_time, 0, 'f', 3))
            child_item.setTextAlignment(1, Qt.AlignCenter)

            child_item.setToolTip(2,_('Local time in function '\
                                      '(not in sub-functions)'))
            #child_item.setData(2, Qt.DisplayRole, loc_time)
            child_item.setData(2, Qt.DisplayRole,
                              QString('%1').arg(loc_time, 0, 'f', 3))
            child_item.setTextAlignment(2,Qt.AlignCenter)

            child_item.setToolTip(3, _('Total number of calls '\
                                       '(including recursion)'))
            child_item.setData(3, Qt.DisplayRole, total_calls)
            child_item.setTextAlignment(3, Qt.AlignCenter)

            child_item.setToolTip(4, _('File:line '\
                                       'where function is defined'))
            child_item.setData(4, Qt.DisplayRole, file_and_line)
            #child_item.setExpanded(True)
            if self.is_recursive(child_item):
                child_item.setData(4, Qt.DisplayRole, '(%s)' % _('recursion'))
                child_item.setDisabled(True)
            else:
                self.populate_tree(child_item, self.find_callees(child_key))
            self.item_depth -= 1
    
    def is_recursive(self, child_item):
        """Returns True is a function is a descendant of itself."""
        ancestor = child_item.parent()
        # FIXME: indexes to data should be defined by a dictionary on init
        while ancestor:
            if (child_item.data(0, Qt.DisplayRole
                                ) == ancestor.data(0, Qt.DisplayRole) and
                child_item.data(4, Qt.DisplayRole
                                ) == ancestor.data(4, Qt.DisplayRole)):
                return True
            else:
                ancestor = ancestor.parent()
        return False
            
    def change_view(self, change_in_depth):
        """Change the view depth by expand or collapsing all same-level nodes"""
        self.current_view_depth += change_in_depth
        if self.current_view_depth < 1:
            self.current_view_depth = 1
        for item in self.item_list:
            item_depth = item.data(0, Qt.UserRole).toInt()[0]
            item.setExpanded(item_depth < self.current_view_depth)                
    

def test():
    """Run widget test"""
    from spyderlib.utils.qthelpers import qapplication
    app = qapplication()
    widget = ProfilerWidget(None)
    widget.show()
    #widget.analyze(__file__)
    widget.analyze('../../rope_profiling/test.py')
    sys.exit(app.exec_())
    
if __name__ == '__main__':
    test()
