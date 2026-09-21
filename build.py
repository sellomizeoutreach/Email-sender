"""
build.py - Automated packaging pipeline for Sellomize Reach.

Usage:
    python build.py               # Full build: PyInstaller + Inno Setup installer
    python build.py --clean       # Clean previous build/dist folders first
    python build.py --no-installer # Skip Inno Setup, only build PyInstaller dist
"""

import os
import sys
import shutil
import subprocess
import argparse

WORKSPACE_ROOT = os.path.dirname(os.path.abspath(__file__))
ISCC_CANDIDATES = [
    os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Inno Setup 6', 'ISCC.exe'),
    r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    r'C:\Program Files\Inno Setup 6\ISCC.exe',
    shutil.which('iscc')
]

def find_iscc() -> str:
    for candidate in ISCC_CANDIDATES:
        if candidate and os.path.exists(candidate):
            return candidate
    return ''

def main():
    parser = argparse.ArgumentParser(description='Build Sellomize Reach Desktop Application')
    parser.add_argument('--clean', action='store_true', help='Clean build/ and dist/ folders before building')
    parser.add_argument('--no-installer', action='store_true', help='Skip Inno Setup installer compilation')
    args = parser.parse_args()

    os.chdir(WORKSPACE_ROOT)
    print('=== Sellomize Reach Build Pipeline ===')
    print(f'Workspace: {WORKSPACE_ROOT}')

    if args.clean:
        print('\n[1/3] Cleaning previous build artifacts...')
        for folder in ['build', 'dist']:
            folder_path = os.path.join(WORKSPACE_ROOT, folder)
            if os.path.exists(folder_path):
                try:
                    shutil.rmtree(folder_path)
                    print(f'  Removed {folder}/')
                except Exception as e:
                    print(f'  Warning: Could not remove {folder}/: {e}')
    else:
        print('\n[1/3] Skipping clean (use --clean to purge previous artifacts)')

    print('\n[2/3] Compiling application with PyInstaller...')
    spec_path = os.path.join(WORKSPACE_ROOT, 'sellomize.spec')
    if not os.path.exists(spec_path):
        print(f'Error: Spec file not found at {spec_path}')
        sys.exit(1)

    pyinstaller_cmd = [sys.executable, '-m', 'PyInstaller', '--clean' if args.clean else '--noconfirm', 'sellomize.spec']
    print('  Running:', ' '.join(pyinstaller_cmd))
    res = subprocess.run(pyinstaller_cmd, cwd=WORKSPACE_ROOT)
    if res.returncode != 0:
        print(f'PyInstaller build failed with exit code {res.returncode}')
        sys.exit(res.returncode)

    exe_path = os.path.join(WORKSPACE_ROOT, 'dist', 'SellomizeReach', 'SellomizeReach.exe')
    if not os.path.exists(exe_path):
        print(f'Error: Expected executable not found at {exe_path}')
        sys.exit(1)
    print(f'  PyInstaller build successful: {exe_path}')

    if args.no_installer:
        print('\n[3/3] Inno Setup compilation skipped (--no-installer).')
        print('\nBuild completed successfully.')
        return

    print('\n[3/3] Compiling Inno Setup installer...')
    iscc_bin = find_iscc()
    if not iscc_bin:
        print('  Warning: Inno Setup compiler (ISCC.exe) not found. Skipping installer generation.')
        print('  PyInstaller output is ready at dist/SellomizeReach.')
        return

    iss_path = os.path.join(WORKSPACE_ROOT, 'installer.iss')
    if not os.path.exists(iss_path):
        print(f'Error: installer.iss not found at {iss_path}')
        sys.exit(1)

    os.makedirs(os.path.join(WORKSPACE_ROOT, 'Output'), exist_ok=True)
    iscc_cmd = [iscc_bin, iss_path]
    print(f'  Found ISCC: {iscc_bin}')
    print('  Running:', ' '.join(iscc_cmd))
    res = subprocess.run(iscc_cmd, cwd=WORKSPACE_ROOT)
    if res.returncode != 0:
        print(f'Inno Setup compilation failed with exit code {res.returncode}')
        sys.exit(res.returncode)

    setup_exe = os.path.join(WORKSPACE_ROOT, 'Output', 'SellomizeSetup.exe')
    if os.path.exists(setup_exe):
        print(f'  Installer built successfully: {setup_exe}')
    print('\nFull build completed successfully.')

if __name__ == '__main__':
    main()
