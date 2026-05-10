import argparse
import os
import re

# --- QPT monkey-patch 1: PowerShell 路径空格兼容 ---
# QPT 在构造 PowerShell 命令时不对含空格的路径加引号，导致 CommandNotFoundException
import qpt.kernel.qterminal as _qterm

_ORIG_SHELL_FUNC = _qterm.PTerminal._shell_func


def _ps_quote_paths_in_command(cmd: str) -> str:
    """为包含空格的 Windows 绝对路径添加 PowerShell 引号和 & 调用符"""
    # 1) 引用可执行文件路径 (X:\...\xxx.exe)
    exe_match = re.match(r'^([A-Za-z]:\\[^"]*?\.(?:exe|bat|cmd))', cmd)
    if exe_match:
        exe_path = exe_match.group(1)
        if ' ' in exe_path:
            rest = cmd[len(exe_path):]
            cmd = f'& "{exe_path}"{rest}'

    # 2) 引用 flag 后面的路径参数 (--target, -d, -r, -f, --cache-dir, --find-links)
    path_flags = ['--target', '--cache-dir', '--find-links', '-d', '-r', '-f']
    for flag in path_flags:
        pattern = re.escape(flag) + r'\s+([A-Za-z]:\\[^\n]*?)(?=\s+--|\s+-[a-zA-Z]|\s*$)'

        def _replacer(m, _flag=flag):
            path = m.group(1)
            if ' ' in path and not path.startswith('"'):
                return f'{_flag} "{path}"'
            return m.group(0)

        cmd = re.sub(pattern, _replacer, cmd)
    return cmd


def _patched_shell_func(self, callback=None):
    if callback is None:
        callback = _qterm.LoggingTerminalCallback()

    def closure(closure_shell):
        _qterm.Logging.debug(f"SHELL: {closure_shell}")

        drive_prefix = ""
        if len(closure_shell) > 2 and closure_shell[1:3] == ":\\":
            drive_prefix = f"cd {closure_shell[:2]}\\ ; "

        closure_shell = _ps_quote_paths_in_command(closure_shell)
        closure_shell = drive_prefix + closure_shell
        closure_shell += f' ; echo "---QPT OUTPUT STATUS CODE---" $? \n'

        try:
            final_shell = closure_shell.encode("utf-8")
        except Exception as e:
            _qterm.Logging.error(
                "执行该指令时遇到解码问题，目前将采用兼容模式进行，原始报错如下：\n" + str(e))
            final_shell = closure_shell.encode("utf-8", errors="ignore")

        try:
            self.main_terminal.stdin.write(final_shell)
        except OSError as e:
            if self.first_flag:
                _qterm.Logging.warning(
                    "当前操作系统可能无法正常调起Powershell，如您正在使用盗版/已被破坏的Windows操作系统，"
                    f"强烈建议您进行更新！\n当前正在尝试在线补充Powershell，原始报错信息如下\n{e}")
                _qterm.Logging.info("正在下载Powershell 5")
                _qterm.download(
                    url="https://bj.bcebos.com/v1/ai-studio-online/1c4c1b9fd52c49f3b88697e60f"
                        "771d1e1181711684b84c7bb830cb589d1689ee?responseContentDisposition=at"
                        "tachment%3B%20filename%3Dpwsh.zip",
                    file_name="pwsh.zip",
                    path=_qterm.TMP_BASE_PATH)
                import zipfile
                zip_path = os.path.join(_qterm.TMP_BASE_PATH, "pwsh.zip")
                pwsh_dir = os.path.join(_qterm.TMP_BASE_PATH, "pwsh_ext")
                with zipfile.ZipFile(zip_path) as zip_obj:
                    zip_obj.extractall(pwsh_dir)
                os_env = _qterm.QPT_MEMORY.get_env_vars().copy()
                os_env["PATH"] += f"{pwsh_dir};"
                _qterm.QPT_MEMORY.set_mem(name="get_env_vars", variable=os_env)
                self.reset_terminal()
                self.first_flag = False
            else:
                _qterm.Logging.error("当前操作系统仍无法正常调起Powershell，程序已终止！")
                exit(-200)
        try:
            self.main_terminal.stdin.flush()
        except Exception as e:
            _qterm.Logging.error(str(e))
        callback.handle(self.main_terminal)

    return closure


_qterm.PTerminal._shell_func = _patched_shell_func
# --- end PowerShell path fix ---

# --- QPT monkey-patch 3: 兼容 Python 3.11+ / pip 23+ ---
# 必须在 import qinterpreter 之前执行：qinterpreter→qcode 会 `from qpackage import search_dep`，
# 若晚于 qinterpreter 再改 qpackage.search_dep，qcode 里仍绑定旧的 search_dep，导致 pkg.requires() 报错。
import qpt.kernel.qpackage as _qpkg


def _patched_search_dep():
    import re as _re
    pkgs = _qpkg.get_installed_distributions()
    pkg_dict = _qpkg.WhlDict()
    for pkg in pkgs:
        pkg_name = getattr(pkg, 'project_name', None) or getattr(pkg, 'name', None) or getattr(pkg, 'canonical_name', str(pkg))
        requires = getattr(pkg, 'requires', None)
        dep = requires() if callable(requires) else requires
        if dep:
            dep_dict = _qpkg.WhlDict()
            for d in dep:
                d_str = str(d)
                if 'extra ==' in d_str or 'extra==' in d_str:
                    continue
                if hasattr(d, 'hashCmp'):
                    d_name, _, d_version = d.hashCmp[:3]
                    dep_dict[d_name] = str(d_version) if d_version else None
                elif isinstance(d, str):
                    m = _re.match(r'^([A-Za-z0-9_.-]+)', d)
                    dep_dict[m.group(1) if m else d] = None
                else:
                    d_name = getattr(d, 'project_name', getattr(d, 'name', str(d)))
                    dep_dict[d_name] = None
            pkg_dict[pkg_name] = dep_dict
        else:
            pkg_dict[pkg_name] = None
    return pkg_dict


_qpkg.search_dep = _patched_search_dep
# --- end search_dep fix ---

# --- QPT monkey-patch 2: pip 超时修正 ---
# QPT 默认 --timeout 10 太短，大包下载容易 ReadTimeoutError
import qpt.kernel.qinterpreter as _qinterp

_orig_pip_shell = _qinterp.PipTools.pip_shell


def _patched_pip_shell(self, shell: str):
    """将 pip timeout 从 10 秒提升到 300 秒"""
    if not os.path.exists(_qinterp.get_qpt_tmp_path('pip_cache')):
        os.makedirs(_qinterp.get_qpt_tmp_path('pip_cache'), exist_ok=True)
    shell += (f" --isolated --disable-pip-version-check"
              f" --cache-dir {_qinterp.get_qpt_tmp_path('pip_cache')}"
              f" --timeout 300 --prefer-binary")
    if self.quiet:
        shell += " --quiet"
    self.pip_main(str(shell).split(" "))
    _qinterp.clean_stout(['console', 'console_errors', 'console_subprocess'])


_qinterp.PipTools.pip_shell = _patched_pip_shell
# --- end pip timeout fix ---

# --- QPT monkey-patch 4: 排除不需要的依赖包 ---
# search_dep 的 extra== 过滤已解决大部分问题，以下作为安全网
# 排除需要系统级编译工具或明确不需要的包
QPT_EXCLUDE_PACKAGES = {
    "scikit_umfpack",
    "gdal",
    "pygraphviz",
    "pycairo",
    "cairocffi",
    "xattr",
}

_orig_flatten = _qinterp.PipTools.flatten_requirements


def _patched_flatten_requirements(requirements: dict):
    result = _orig_flatten(requirements)
    exclude_normalized = {p.lower().replace("-", "_") for p in QPT_EXCLUDE_PACKAGES}
    for pkg in list(result.keys()):
        if pkg.lower().replace("-", "_") in exclude_normalized:
            del result[pkg]
    return result


_qinterp.PipTools.flatten_requirements = staticmethod(_patched_flatten_requirements)
# --- end dependency exclusion fix ---

# --- QPT monkey-patch 5: setup_install 模式下跳过 CheckCompileCompatibility ---
# setup_install 模式直接在线安装，不会生成 requirements_dev.txt，
# 但 CheckCompileCompatibilityOpt 仍会尝试读取该文件导致 FileNotFoundError
from qpt.modules.package import CheckCompileCompatibilityOpt as _CheckCCOpt

_orig_check_cc_act = _CheckCCOpt.act


def _patched_check_cc_act(self):
    req_path = os.path.join(self.opt_path, "requirements_dev.txt")
    if not os.path.isfile(req_path):
        return
    _orig_check_cc_act(self)


_CheckCCOpt.act = _patched_check_cc_act
# --- end CheckCompileCompatibility fix ---

from qpt.executor import CreateExecutableModule as CEM
from qpt.smart_opt import set_default_pip_source
from qpt.kernel.qinterpreter import PYPI_PIP_SOURCE, DISPLAY_SETUP_INSTALL
from qpt.modules.package import CustomPackage, DEFAULT_DEPLOY_MODE

LITE_EXCLUDE_PACKAGES = [
    "torch",
    "torchvision",
    "paddlepaddle",
    "paddlepaddle-gpu",
    "paddleocr",
    "onnxruntime",
    "onnxruntime-gpu",
    "onnxruntime-directml",
]


def _create_lite_requirements(work_dir: str) -> str:
    """生成精简版 requirements.txt，排除重型依赖"""
    src = os.path.join(work_dir, "requirements.txt")
    lines = []
    with open(src, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
                continue
            pkg_name = stripped.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
            if pkg_name.lower() in [p.lower() for p in LITE_EXCLUDE_PACKAGES]:
                lines.append(f"# [lite excluded] {line}")
            else:
                lines.append(line)

    lite_path = os.path.join(work_dir, "requirements_lite.txt")
    with open(lite_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return lite_path


def main():
    WORK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    LAUNCH_PATH = os.path.join(WORK_DIR, 'gui.py')
    SAVE_PATH = os.path.join(os.path.dirname(WORK_DIR), 'vsr_out')
    ICON_PATH = os.path.join(WORK_DIR, "design", "vsr.ico")

    parser = argparse.ArgumentParser(description="打包程序")
    parser.add_argument(
        "--cuda",
        nargs="?",
        const="11.8",
        default=None,
        help="是否包含CUDA模块，可指定版本，如 --cuda 或 --cuda=11.8"
    )
    parser.add_argument(
        "--directml",
        nargs="?",
        const=True,
        default=None,
        help="是否使用DirectML加速，仅指定 --directml 即可启用"
    )
    parser.add_argument(
        "--lite",
        action="store_true",
        default=False,
        help="轻量打包模式: 排除 torch/paddle 等重型依赖，用户通过界面在线安装"
    )

    args = parser.parse_args()

    sub_modules = []

    if not args.lite:
        if args.cuda == "11.8":
            sub_modules.append(CustomPackage("torch==2.7.0 torchvision==0.22.0", deploy_mode=DEFAULT_DEPLOY_MODE, find_links=PYPI_PIP_SOURCE, opts="--index-url https://download.pytorch.org/whl/cu118 "))
        elif args.cuda == "12.6":
            sub_modules.append(CustomPackage("torch==2.7.0 torchvision==0.22.0", deploy_mode=DEFAULT_DEPLOY_MODE, find_links=PYPI_PIP_SOURCE, opts="--index-url https://download.pytorch.org/whl/cu126 "))
        elif args.cuda == "12.8":
            sub_modules.append(CustomPackage("torch==2.7.0 torchvision==0.22.0", deploy_mode=DEFAULT_DEPLOY_MODE, find_links=PYPI_PIP_SOURCE, opts="--index-url https://download.pytorch.org/whl/cu128 "))

        if args.directml:
            sub_modules.append(CustomPackage("torch_directml==0.2.5.dev240914", deploy_mode=DEFAULT_DEPLOY_MODE))

    if os.getenv("QPT_Action") == "True":
        set_default_pip_source(PYPI_PIP_SOURCE)

    requirements_file = "./requirements.txt"
    if args.lite:
        requirements_file = _create_lite_requirements(WORK_DIR)
        SAVE_PATH = os.path.join(os.path.dirname(WORK_DIR), 'vsr_out_lite')
        print(f"[Lite 模式] 重型依赖已排除，用户将通过依赖安装界面在线安装")
        print(f"[Lite 模式] 使用精简依赖文件: {requirements_file}")
        print(f"[Lite 模式] 输出目录: {SAVE_PATH}")

    module = CEM(
        work_dir=WORK_DIR,
        launcher_py_path=LAUNCH_PATH,
        save_path=SAVE_PATH,
        icon=ICON_PATH,
        hidden_terminal=False,
        requirements_file=requirements_file,
        deploy_mode=DISPLAY_SETUP_INSTALL,
        sub_modules=sub_modules,
    )

    module.make()

    # --- QPT bug 修复: sitecustomize.py 中 os.environ.get("QPT_MODE") 可能返回 None ---
    release_dir = os.path.join(SAVE_PATH, "Release")
    sitecustomize_path = os.path.join(
        release_dir, "Python", "Lib", "site-packages", "sitecustomize.py"
    )
    if os.path.isfile(sitecustomize_path):
        with open(sitecustomize_path, "r", encoding="utf-8") as f:
            content = f.read()
        patched = content.replace(
            'os.environ.get("QPT_MODE")',
            'os.environ.get("QPT_MODE", "")',
        )
        if patched != content:
            with open(sitecustomize_path, "w", encoding="utf-8") as f:
                f.write(patched)
            print("[Post-build] 已修复 sitecustomize.py 中 QPT_MODE 环境变量为 None 的 bug")


if __name__ == '__main__':
    main()