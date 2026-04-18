-- ユーザー登録情報をすべて削除するスクリプト
-- 警告: このスクリプトはすべてのユーザーデータを削除します

-- 1. 関連データを削除 (ON DELETE CASCADEで自動削除されますが、念のため)
DELETE FROM public.ocr_usage;
DELETE FROM public.bank_records;
DELETE FROM public.bets;
DELETE FROM public.predictions;

-- 2. プロファイルを削除
DELETE FROM public.profiles;

-- 3. 認証ユーザーを削除 (auth.users)
-- 注意: これはSupabase Dashboardから実行する必要があります
-- SQLエディタでは auth スキーマへの直接アクセスが制限されている場合があります

-- 確認メッセージ
SELECT 'All user data has been deleted' AS status;
