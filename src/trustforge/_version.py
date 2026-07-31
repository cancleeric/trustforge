"""TrustForge 版號（build 時被 deploy/deploy_ec2.sh 覆寫進 zip 複本）。

紀律：repo 這份不得 commit 被 deploy 腳本蓋過的 git 版號——deploy 是整檔重寫
（deploy/deploy_ec2.sh:217），所以這裡放的是「宣告版號」，deploy 會把它細化成
git describe 的結果。

原本這支硬寫 VERSION = "dev"，於是 /api/health 永遠回 {"version": "dev"}，
前端版號徽章也就永遠顯示不出版號——使用者回報「系統沒顯示版號」正是這個。
`VERSION` 是 repository 唯一的 canonical release version。pyproject 透過
setuptools dynamic attr 讀取它，`trustforge.__version__` 也直接 import 它；
frontend 的 npm metadata 則由 release_version.py 產生並驗證。

注意不要改用 importlib.metadata 去問：實際跑 `python -m trustforge.web` 的
直譯器（homebrew 3.14）沒有安裝 trustforge 的 package metadata，實測
`version("trustforge")` 直接 PackageNotFoundError，等於又退回 "dev"，
換湯不換藥。所以這裡保留可由 build backend 靜態解析的單一字面值。
"""

VERSION = "0.27.43"
