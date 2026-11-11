"""Small Tk desktop launcher. Capture and inference run in separate child processes."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from .sessions import create_session, finish_session, session_roots, update_index, atomic_json
from .agent import endpoint_options

PROJECT = Path(__file__).resolve().parent.parent
SETTINGS = PROJECT / 'desktop-settings.json'


def load_settings():
    defaults = {'data': str(PROJECT / 'data'), 'provider': 'ollama',
                'ollama_endpoint': 'http://127.0.0.1:11434/api/chat', 'ollama_model':'collector-qwen:16k',
                'openai_endpoint': 'https://api.openai.com/v1/chat/completions', 'openai_model':''}
    try:
        values = json.loads(SETTINGS.read_text(encoding='utf-8'))
        defaults.update({k:v for k,v in values.items() if k in defaults and isinstance(v,str)})
    except (OSError, ValueError):
        pass
    if defaults['provider'] not in ('ollama','openai'):
        defaults['provider']='ollama'
    return defaults


def python_executable():
    path = Path(sys.executable)
    return str(path.with_name('python.exe')) if path.name.lower() == 'pythonw.exe' else str(path)


class Desktop:
    def __init__(self, root):
        self.root = root
        self.settings = load_settings()
        self.messages = queue.Queue()
        self.capture = self.analysis = None
        self.capture_root = None
        self.finalizing = self.analyzing = self.closing = False
        self.cancel_analysis = threading.Event()
        self.data = tk.StringVar(value=self.settings['data'])
        self.provider = tk.StringVar(value=self.settings['provider'])
        self.endpoint = tk.StringVar()
        self.model = tk.StringVar()
        self.key = tk.StringVar()
        self.status = tk.StringVar(value='就绪')
        self.active_provider = self.provider.get()
        root.title('个人经历采集器')
        root.geometry('920x820')
        root.minsize(860,760)
        panel = ttk.Frame(root,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='个人经历采集器',font=('',18,'bold')).pack(anchor='w')
        paths=ttk.Frame(panel);paths.pack(fill='x',pady=12)
        ttk.Label(paths,text='保存目录').pack(side='left')
        self.path_entry=ttk.Entry(paths,textvariable=self.data);self.path_entry.pack(side='left',fill='x',expand=True,padx=8)
        self.browse=ttk.Button(paths,text='选择…',command=self.choose);self.browse.pack(side='left')
        ttk.Button(paths,text='打开目录',command=lambda:self.open_path(Path(self.data.get()))).pack(side='left',padx=4)
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        self.start_button=ttk.Button(buttons,text='开始采集并隐藏',command=self.start_capture);self.start_button.pack(side='left')
        self.stop_button=ttk.Button(buttons,text='停止采集',command=self.stop_capture,state='disabled');self.stop_button.pack(side='left',padx=8)
        ttk.Label(panel,text='开始后窗口隐藏；右键蓝色托盘图标 → 打开主窗口。暂停/恢复也在托盘中。').pack(anchor='w',pady=6)
        config=ttk.LabelFrame(panel,text='分析设置',padding=10);config.pack(fill='x',pady=8)
        for label,value in [('本地 Ollama','ollama'),('API（OpenAI 兼容）','openai')]:
            ttk.Radiobutton(config,text=label,value=value,variable=self.provider,command=self.switch_provider).pack(side='top',anchor='w')
        for label,var in [('接口地址',self.endpoint),('模型名称',self.model),('API Key',self.key)]:
            line=ttk.Frame(config);line.pack(fill='x',pady=3)
            ttk.Label(line,text=label,width=12).pack(side='left')
            entry=ttk.Entry(line,textvariable=var,show='*' if var is self.key else '')
            entry.pack(side='left',fill='x',expand=True)
            if var is self.key:self.key_entry=entry
        ttk.Label(config,text='API 模式将所选批次的文字与截图发送到指定服务。密钥仅本次运行使用，不保存到文件。').pack(anchor='w')
        self.switch_provider(initial=True)
        bar=ttk.Frame(panel);bar.pack(fill='x',pady=6)
        ttk.Label(bar,text='选择需要分析或查看的批次（可多选）').pack(side='left')
        ttk.Button(bar,text='刷新列表',command=self.refresh).pack(side='right')
        self.tree=ttk.Treeview(panel,columns=('time','state','path'),show='headings',height=5,selectmode='extended')
        for name,label,width in [('time','采集开始时间',210),('state','状态',85),('path','批次目录',380)]:
            self.tree.heading(name,text=label);self.tree.column(name,width=width)
        self.tree.pack(fill='both',expand=True)
        bar=ttk.Frame(panel);bar.pack(fill='x',pady=8)
        self.analyze_button=ttk.Button(bar,text='开始分析所选批次',command=self.start_analysis);self.analyze_button.pack(side='left')
        self.cancel_button=ttk.Button(bar,text='停止分析',command=self.stop_analysis,state='disabled');self.cancel_button.pack(side='left',padx=6)
        ttk.Button(bar,text='查看结果',command=self.open_result).pack(side='left')
        ttk.Button(bar,text='打开批次文件夹',command=self.open_selected).pack(side='left',padx=6)
        ttk.Button(bar,text='退出软件',command=self.quit).pack(side='right')
        ttk.Label(panel,textvariable=self.status).pack(anchor='w')
        self.log=tk.Text(panel,height=5,wrap='word',state='disabled');self.log.pack(fill='both',pady=5)
        root.protocol('WM_DELETE_WINDOW',self.window_close)
        self.refresh()
        root.after(250,self.poll)

    def choose(self):
        chosen=filedialog.askdirectory(initialdir=self.data.get())
        if chosen:self.data.set(chosen);self.save();self.refresh()

    def save(self):
        self.settings.update(data=str(Path(self.data.get()).expanduser().resolve()),provider=self.provider.get())
        self.settings[self.active_provider+'_endpoint']=self.endpoint.get().strip()
        self.settings[self.active_provider+'_model']=self.model.get().strip()
        atomic_json(SETTINGS,self.settings)

    def switch_provider(self,initial=False):
        if not initial:
            self.settings[self.active_provider+'_endpoint']=self.endpoint.get()
            self.settings[self.active_provider+'_model']=self.model.get()
        self.active_provider=self.provider.get()
        self.endpoint.set(self.settings[self.active_provider+'_endpoint'])
        self.model.set(self.settings[self.active_provider+'_model'])
        self.key_entry.configure(state='normal' if self.active_provider=='openai' else 'disabled')

    def refresh(self):
        selected=set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        base=Path(self.data.get()).expanduser().resolve()
        roots=session_roots(base)
        if (base/'events.sqlite3').exists() and not (base/'session.json').exists():roots.insert(0,base)
        for folder in reversed(roots):
            try:
                metadata=json.loads((folder/'session.json').read_text(encoding='utf-8')) if (folder/'session.json').exists() else {}
                self.tree.insert('', 'end', iid=str(folder),values=(metadata.get('local_started_at',metadata.get('started_at','旧版数据')),metadata.get('status','legacy'),str(folder)))
            except (OSError,ValueError):continue
        available=self.tree.get_children()
        restore=[x for x in available if x in selected]
        if restore:self.tree.selection_set(restore)
        elif available:self.tree.selection_set(available[0])

    def open_path(self,path):
        try:
            if not path.exists():raise ValueError('文件尚未生成：'+str(path))
            if os.name=='nt':os.startfile(str(path))
            else:subprocess.Popen(['xdg-open',str(path)])
        except Exception as exc:messagebox.showerror('无法打开',str(exc))

    def selected(self):
        items=self.tree.selection()
        if not items:raise ValueError('请先选择一个采集批次')
        return [Path(x) for x in items]

    def open_selected(self):
        try:self.open_path(self.selected()[0])
        except ValueError as exc:messagebox.showinfo('提示',str(exc))

    def open_result(self):
        try:self.open_path(self.selected()[0]/'experiences/timeline.html')
        except ValueError as exc:messagebox.showinfo('提示',str(exc))

    def spawn(self,args,env=None):
        proc=subprocess.Popen([python_executable(),'-u','-m',*args],cwd=PROJECT,env=env,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        def read():
            for line in proc.stdout:self.messages.put(('log',line.rstrip()))
            proc.stdout.close()
        threading.Thread(target=read,daemon=True).start()
        return proc

    def start_capture(self):
        if self.capture or self.finalizing:return
        try:
            self.save()
            base=Path(self.settings['data'])
            self.capture_root=create_session(base)
            self.capture_base=base
            self.capture=self.spawn(['collector','--data',str(base),'--session-dir',str(self.capture_root)])
            self.start_button.configure(state='disabled');self.stop_button.configure(state='normal')
            self.path_entry.configure(state='disabled');self.browse.configure(state='disabled')
            self.status.set('正在启动采集…')
            # Keep errors visible during startup; hide only after the ready line.
        except Exception as exc:messagebox.showerror('启动失败',str(exc))

    def stop_capture(self):
        if self.capture_root and self.capture and self.capture.poll() is None:
            (self.capture_root/'stop.request').touch()
            self.status.set('正在停止并保存采集…')

    def start_analysis(self):
        if self.analyzing:return
        try:
            folders=self.selected()
            provider=self.provider.get();endpoint=self.endpoint.get().strip();model=self.model.get().strip()
            if not model:raise ValueError('请填写完整模型名称')
            key=self.key.get().strip() if provider=='openai' else ''
            endpoint_options(endpoint,key)
            suffix='/api/chat' if provider=='ollama' else '/chat/completions'
            if not endpoint.endswith(suffix):raise ValueError('请填写完整接口地址，以 '+suffix+' 结尾')
            self.save()
            env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';env['COLLECTOR_AGENT_KEY']=key
            self.cancel_analysis.clear();self.analyzing=True
            self.analyze_button.configure(state='disabled');self.cancel_button.configure(state='normal')
            self.status.set('正在分析所选批次…')
            def run():
                try:
                    for folder in folders:
                        if self.cancel_analysis.is_set():break
                        self.analysis=self.spawn(['collector.pipeline','analyze','--data',str(folder),
                            '--provider',provider,'--endpoint',endpoint,'--model',model],env)
                        if self.cancel_analysis.is_set():self.analysis.terminate()
                        code=self.analysis.wait()
                        self.messages.put(('log',f'批次分析进程结束（退出码 {code}）：{folder.name}'))
                except Exception as exc:self.messages.put(('log','分析启动失败：'+str(exc)))
                finally:self.analysis=None;self.messages.put(('analysis_done',None))
            threading.Thread(target=run,daemon=True).start()
        except Exception as exc:messagebox.showerror('无法开始分析',str(exc))

    def stop_analysis(self):
        self.cancel_analysis.set()
        if self.analysis and self.analysis.poll() is None:self.analysis.terminate()
        self.status.set('分析已请求停止；未完成记录保留')

    def window_close(self):
        if self.capture and self.capture.poll() is None:self.root.withdraw()
        elif self.analyzing:self.root.iconify()
        else:self.quit()

    def quit(self):
        self.closing=True;self.stop_analysis();self.stop_capture()
        if not self.capture and not self.analyzing and not self.finalizing:self.root.destroy()

    def poll(self):
        while not self.messages.empty():
            kind,value=self.messages.get()
            if kind=='log':
                self.log.configure(state='normal');self.log.insert('end',value+'\n');self.log.see('end');self.log.configure(state='disabled')
                if value.startswith('正在采集') and self.capture and not self.closing:
                    self.status.set('正在采集，托盘可打开主窗口');self.root.withdraw()
            elif kind=='analysis_done':
                self.analyzing=False;self.analyze_button.configure(state='normal');self.cancel_button.configure(state='disabled')
                self.status.set('分析进程已结束；请查看日志及批次结果');self.refresh()
            elif kind=='capture_done':
                self.finalizing=False;self.start_button.configure(state='normal');self.stop_button.configure(state='disabled')
                self.path_entry.configure(state='normal');self.browse.configure(state='normal')
                self.status.set(value);self.refresh()
                if not self.closing:self.root.deiconify()
        if self.capture_root:
            request=self.capture_root/'show-window.request'
            if request.exists():
                request.unlink();self.root.deiconify();self.root.lift()
        if self.capture and self.capture.poll() is not None:
            code=self.capture.returncode;self.capture=None;self.finalizing=True
            folder,base=self.capture_root,self.capture_base
            def finalize():
                try:
                    finish_session(folder,interrupted=code!=0)
                    from .experiences import build
                    from .transport import export_bundle
                    build(folder,folder/'experiences');update_index(base)
                    export_bundle(folder,folder/('capture-'+str(time.time_ns())+'.zip'))
                    self.messages.put(('capture_done','采集结束，已保存并打包'))
                except Exception as exc:self.messages.put(('capture_done','保存后处理失败：'+str(exc)))
            threading.Thread(target=finalize,daemon=True).start()
        if self.closing and not self.capture and not self.analyzing and not self.finalizing:
            self.root.destroy();return
        self.root.after(250,self.poll)


def main():
    os.environ['PYTHONIOENCODING']='utf-8'
    root=tk.Tk()
    try:
        Desktop(root)
        root.mainloop()
    except Exception:
        import traceback
        (PROJECT / 'desktop-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        messagebox.showerror('窗口启动失败', '详细错误已保存到 desktop-error.log')
        root.destroy()

if __name__=='__main__':main()
