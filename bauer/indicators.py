"""Modern execution indicators for Bauer Agent."""
from __future__ import annotations
import contextlib
import sys
import time
if sys.platform == 'win32':
    if sys.stdout is not None: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if sys.stderr is not None: sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.style import Style
from rich.text import Text

ACCENT = '#00d4aa'; PULSE = '#7c3aed'; DIM = '#6b7280'; WHITE = '#f9fafb'; SUCCESS = '#10b981'; ERROR_COLOR = '#ef4444'
ACCENT_STYLE = Style(color=ACCENT); DIM_STYLE = Style(color=DIM); WHITE_STYLE = Style(color=WHITE)
MODERN_SPINNER = 'dots12'

class ExecutionIndicator:
    def __init__(self, console=None, mode='spin', description='', transient=True):
        self.console = console or Console(); self.mode = mode; self.description = description; self.transient = transient
        self._progress = None; self._task_id = None; self._start_time = 0.0
    def __enter__(self): self.start(); return self
    def __exit__(self, *args): self.stop()
    def start(self, description=''):
        if description: self.description = description
        self._start_time = time.time()
        if self.mode == 'progress':
            self._progress = Progress(SpinnerColumn(spinner_name=MODERN_SPINNER, style=ACCENT_STYLE), TextColumn('{task.description}', style=WHITE_STYLE), BarColumn(bar_width=24, style=DIM_STYLE, completed_style=ACCENT_STYLE), TextColumn('{task.percentage:>3.0f}%', style=ACCENT_STYLE), TimeElapsedColumn(), console=self.console, transient=self.transient)
            self._progress.__enter__(); self._task_id = self._progress.add_task(self.description, total=100)
        else:
            self._progress = Progress(SpinnerColumn(spinner_name=MODERN_SPINNER, style=ACCENT_STYLE), TextColumn('{task.description}', style=WHITE_STYLE), TimeElapsedColumn(), console=self.console, transient=self.transient)
            self._progress.__enter__(); self._task_id = self._progress.add_task(self.description, total=None)
    def update(self, description='', advance=0):
        if self._progress and self._task_id is not None:
            if description: self._progress.update(self._task_id, description=description)
            if advance and self.mode == 'progress': self._progress.update(self._task_id, advance=advance)
    def complete(self, result='OK Concluido'):
        if self._progress and self._task_id is not None: self._progress.update(self._task_id, description=f'[bold {SUCCESS}]{result}[/]')
        self.stop()
    def fail(self, error='X Falhou'):
        if self._progress and self._task_id is not None: self._progress.update(self._task_id, description=f'[bold {ERROR_COLOR}]{error}[/]')
        self.stop()
    def stop(self):
        if self._progress is not None:
            try: self._progress.__exit__(None, None, None)
            except: pass
            self._progress = None; self._task_id = None
    @property
    def elapsed(self): return time.time() - self._start_time if self._start_time else 0.0

@contextlib.contextmanager
def spinning(description='Executando...', console=None, transient=True):
    ind = ExecutionIndicator(console=console, mode='spin', description=description, transient=transient)
    with ind: yield ind

@contextlib.contextmanager
def progress_bar(description='Processando...', console=None, transient=True):
    ind = ExecutionIndicator(console=console, mode='progress', description=description, transient=transient)
    with ind: yield ind

def show_step(step_name, status='running', console=None):
    console = console or Console()
    symbols = {'running': (ACCENT, '>'), 'done': (SUCCESS, 'OK'), 'failed': (ERROR_COLOR, 'XX'), 'skip': (DIM, '--'), 'wait': (PULSE, '..')}
    color, icon = symbols.get(status, symbols['running'])
    text = Text(); text.append(f'  {icon}  ', style=color); text.append(step_name, style='bold')
    console.print(text)

def show_header(title, console=None):
    console = console or Console()
    text = Text(); text.append('  ', style=''); text.append('>', style=ACCENT_STYLE); text.append(f'  {title}', style=WHITE_STYLE)
    console.print(text)
