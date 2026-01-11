# ProtonVPN 接続手順ガイド

## 📥 ステップ1: ProtonVPN のダウンロードとインストール

1. **ProtonVPN公式サイトにアクセス**
   - URL: https://protonvpn.com/
   - 「Get ProtonVPN」をクリック

2. **アカウント作成（無料プラン）**
   - 「Create Free Account」を選択
   - メールアドレスとパスワードを入力
   - アカウント認証を完了

3. **Windows版アプリをダウンロード**
   - ログイン後、「Downloads」ページへ
   - 「ProtonVPN for Windows」をダウンロード
   - インストーラーを実行してインストール

---

## 🔐 ステップ2: VPN接続

1. **ProtonVPNアプリを起動**
   - デスクトップのアイコンまたはスタートメニューから起動
   - 作成したアカウントでログイン

2. **サーバーに接続**
   
   **推奨の接続順序:**
   
   ### パターンA: 日本のサーバー（最優先）
   - 左メニューから「Japan」を選択
   - 無料プランでは利用可能なサーバーが限定されます
   - 「Connect」をクリック
   
   ### パターンB: 別の国のサーバー
   無料プランで利用可能な国:
   - アメリカ (United States)
   - オランダ (Netherlands)  
   - 日本 (Japan)
   
   いずれかに接続してください。

3. **接続確認**
   - 接続が成功すると、緑色の盾マークが表示されます
   - 「Connected to [サーバー名]」と表示されます

---

## ✅ ステップ3: 接続後のテスト実行

### PowerShellを開いて以下のコマンドを実行:

```powershell
# プロジェクトディレクトリに移動
cd C:\Users\yuki2\Documents\ws\keiba-ai-pro

# VPN接続確認テストを実行
C:\Users\yuki2\Documents\ws\keiba\Scripts\python.exe test_after_vpn.py
```

### 期待される結果:

✅ **成功の場合:**
```
✓ 現在のIPアドレス: [新しいIPアドレス]
✓ IPアドレスが変更されました（VPN接続成功）
✓ トップページアクセス成功！
✓ レース情報の取得に成功！
```

❌ **失敗の場合:**
```
✗ まだ400エラーです。別のVPNサーバーを試してください。
```
→ ProtonVPNで別の国のサーバーに接続し直して、再度テスト実行

---

## 🔄 ステップ4: 失敗した場合の対処

### 4-1. 別のサーバーに切り替え

1. ProtonVPNアプリで「Disconnect」をクリック
2. 別の国のサーバーを選択（例: オランダ → アメリカ）
3. 「Connect」をクリック
4. 再度テストスクリプトを実行

### 4-2. 複数のサーバーを試す

無料プランで試せるサーバー:
- ✅ Japan (JP) - 最優先
- ✅ United States (US) - 2番目
- ✅ Netherlands (NL) - 3番目

各サーバーで接続 → テスト → 失敗なら次のサーバー

---

## 📊 ステップ5: 成功後のデータ収集

### VPN経由でのアクセスが成功した場合:

```powershell
# レート制限を守ったテストスクリプト
C:\Users\yuki2\Documents\ws\keiba\Scripts\python.exe test_actual_working_raceid.py
```

### ⚠️ 重要な注意事項:

1. **リクエスト間隔を守る**
   - 各リクエスト間に最低3〜7秒待機
   - 連続リクエストは絶対に避ける

2. **大量リクエストの禁止**
   - 短時間に多数のリクエストを送ると再度ブロックされます
   - 1時間に最大100リクエスト程度を目安に

3. **エラー時は即座に停止**
   - 429 (Too Many Requests) が出たら即座に停止
   - 少なくとも1時間待機してから再開

---

## 🛠️ トラブルシューティング

### ProtonVPNに接続できない場合

1. **Windowsファイアウォールの確認**
   - ProtonVPNアプリを許可リストに追加

2. **再インストール**
   - アプリをアンインストール → 再インストール

3. **別のプロトコルを試す**
   - ProtonVPN設定で接続プロトコルを変更
   - OpenVPN (UDP) → OpenVPN (TCP) → WireGuard

### VPN接続は成功するが、まだ400エラーが出る場合

このVPNサーバーのIPもブロックされている可能性があります:

**次の対策:**
1. ProtonVPNの別のサーバーを全て試す
2. 有料プロキシサービスの利用を検討
   - SmartProxy: $75/月〜
   - 詳細は [IP_BLOCK_SOLUTION.md](IP_BLOCK_SOLUTION.md) 参照

---

## 📝 テスト実行コマンド一覧

### IPアドレス確認のみ
```powershell
C:\Users\yuki2\Documents\ws\keiba\Scripts\python.exe -c "import requests; print(requests.get('https://api.ipify.org').text)"
```

### VPN接続後の総合テスト
```powershell
C:\Users\yuki2\Documents\ws\keiba\Scripts\python.exe test_after_vpn.py
```

### 基本的な接続テスト
```powershell
C:\Users\yuki2\Documents\ws\keiba\Scripts\python.exe test_actual_working_raceid.py
```

---

## 🎯 成功後の次のステップ

1. **スクレイピングサービスの実装**
   - レート制限を実装したスクリプト
   - エラーハンドリングの追加

2. **データ収集の自動化**
   - cron/タスクスケジューラで定期実行
   - 1日1回程度の頻度で

3. **長期的な対策**
   - 有料プロキシサービスへの移行を検討
   - より安定したデータ収集環境の構築

---

## 📞 サポート

問題が解決しない場合:
- [IP_BLOCK_SOLUTION.md](IP_BLOCK_SOLUTION.md) の詳細ガイドを参照
- ProtonVPN公式サポート: https://protonvpn.com/support

---

**現在のIPアドレス（ブロック中）**: `180.46.30.140`  
**VPN接続後は新しいIPに変わります**
