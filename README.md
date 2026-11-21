# AquaMine System Bot

Discordサーバー向けの多機能Botです。「ルール同意による認証ロール付与」「サポートチケット」「レベリング（XP）」の3機能を備えています。

## 機能

### ルール同意（認証）
- `/accept-rules` : サーバールールをEmbedで表示し、「同意する」ボタンを押すと認証ロールが付与されます。30秒に1回まで実行できます。
- `/set-verified-role` : 管理者が、同意時に付与するロールを設定できます。

### チケット
- `/setup-ticket` : 管理者が、チケット用カテゴリ・サポートロール・（任意で）ログチャンネルを設定します。
- `/send-ticket-panel` : 管理者が、チケット作成ボタンのパネルを指定チャンネルに送信します。
- パネルのボタンを押すと、作成者とサポートスタッフだけが見られるプライベートチャンネルが作られます。チケットはDB（`data/tickets.db`）で管理され、閉じると会話のトランスクリプトが `data/transcripts/` に保存され、ログチャンネルにも送信されます。

### レベリング
- メンバーがメッセージを送るとXPが貯まります（スパム防止のため30秒に1回まで、判定はDBに保存されるため再起動してもリセットされません）。
- `/rank` : 自分の順位・レベル・次のレベルまでの進捗を表示します。
- `/leaderboard` : サーバーのXPランキングTOP10を表示します。
- `/set-level-role` : 管理者が、特定レベル到達時に自動付与するロールを設定できます。
- `/remove-level-role` : 管理者が、レベルロールの設定を解除できます。

### その他
- `/sync` : 管理者が、スラッシュコマンドをそのサーバーへ手動同期します。

## セットアップ

### 1. Discord Developer Portal での準備
1. [Discord Developer Portal](https://discord.com/developers/applications) でアプリケーションを作成し、Botのトークンを取得します。
2. Bot の設定で **Privileged Gateway Intents** の **SERVER MEMBERS INTENT** を有効化してください（メンバー取得・レベルロール付与に必要です）。MESSAGE CONTENT INTENT は不要です。
3. Botを対象サーバーに招待します（スラッシュコマンドとロールの管理ができる権限が必要です）。

### 2. 起動（Docker）
1. `.env.sample` を `.env` にコピーし、以下を設定します。
   - `DISCORD_TOKEN` : Botのトークン
   - `GUILD_ID` : （任意）コマンドを即時同期したいサーバーのID。未設定の場合はグローバル同期になります（反映に時間がかかります）。
2. `docker-compose up -d` で起動します。

### 3. 起動（Pythonで直接）
1. Python 3.12 で `pip install -r requirements.txt`
2. `.env` を用意して `python bot.py`

## 設定ファイル
- `config/rules_config.json` : ルールの本文（title / description / color / fields / footer）を編集できます。編集は `/accept-rules` の実行時に読み直されるため、再起動は不要です。`color` は `"#3498db"` のような16進数で指定します。
- `config/guild_settings.json` : 各サーバーの設定（認証ロール、レベルロール）が自動保存されます（ランタイムファイルのためGit管理対象外）。
- `config/ticket_settings.json` : 各サーバーのチケット設定が自動保存されます（同上）。

設定ファイルが壊れた場合は、黙って初期化せずに `<ファイル名>.corrupt` へ退避してから初期値で再作成します。

## データの保存場所
- `data/leveling.db` : レベリングのXPデータ（SQLite）
- `data/tickets.db` : 開いているチケットの管理データ（SQLite）
- `data/transcripts/` : 閉じられたチケットのトランスクリプト

`data/` はDockerのvolumeに含まれているため、コンテナを再作成してもデータは保持されます。`data/` と `config/` のランタイムファイルはGit管理対象外です。

## ライセンス
MIT
