"""TrustForge 版號（build 時被 deploy/deploy_ec2.sh 覆寫進 zip 複本）。

紀律：repo 這份永遠保持 "dev" fallback，別 commit 被 deploy 腳本蓋過的真版號。
"""
VERSION = "dev"
