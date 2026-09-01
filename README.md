# AquaMine System Bot

このボットは、Discordサーバーで「ルール同意」による認証ロール付与を実現するオープンソースボットです。

## 機能
- `/accept-rules` : サーバールールを表示し、同意ボタンを押すと認証ロールが付与されます。
- `/set-verified-role` : 管理者が付与するロールを設定できます。

## 使い方（Docker）

1. `.env.sample` を `.env` にリネームし、`DISCORD_TOKEN` を設定。
2. `docker-compose up -d` で起動。

## 設定ファイル
- `config/rules_config.json` : ルールの本文を編集できます。
- `config/guild_settings.json` : 各サーバーの設定（ロールID）が保存されます。

## ライセンス
MIT