@echo off
cd /d C:\Users\user\trustforge
git pull origin main --no-edit

cd frontend
call npx vite build

powershell -Command "Compress-Archive -Path dist\* -DestinationPath '%TEMP%\frontend-dist.zip' -Force"
aws s3 cp "%TEMP%\frontend-dist.zip" s3://trustforge-deploy-850849012389/frontend-dist.zip --region us-east-1

cd /d C:\Users\user\trustforge
powershell -Command "Compress-Archive -Path src,scripts,data,demo,pyproject.toml,README.md -DestinationPath '%TEMP%\trustforge-deploy.zip' -Force"
aws s3 cp "%TEMP%\trustforge-deploy.zip" s3://trustforge-deploy-850849012389/trustforge-deploy.zip --region us-east-1

aws ssm send-command --instance-ids i-09b03d71e8b740bd0 --document-name "AWS-RunShellScript" --parameters "commands=[\"aws s3 cp s3://trustforge-deploy-850849012389/deploy.sh /tmp/deploy.sh --region us-east-1 && bash /tmp/deploy.sh && aws s3 cp s3://trustforge-deploy-850849012389/deploy_frontend.sh /tmp/deploy_frontend.sh --region us-east-1 && bash /tmp/deploy_frontend.sh\"]" --timeout-seconds 300 --region us-east-1
