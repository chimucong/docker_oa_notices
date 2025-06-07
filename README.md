# 获取OA通知

## 构建
```bash
docker build -t oa_notices .
```

## 配置OA账号
在 docker-compose.yml 中进行配置

## 运行
```bash
docker compose up -d
```

## 获取数据
```bash
curl localhost:5000/notices
```
数据每天 9、12、15、18 点更新

## 强制更新
```bash
curl localhost:5000/refresh_notices
```