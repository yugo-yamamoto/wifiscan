# wifiscan

Windows 用の Wi-Fi スキャナ。周囲のアクセスポイントを **チャネル使用率と接続端末数つき** で一覧表示します。
Python 標準ライブラリだけで動き、`wlanapi.dll` を ctypes で直接叩きます。

> A Wi-Fi scanner for Windows that shows channel utilization and station count per BSS.
> Pure stdlib Python + tkinter, calling `wlanapi.dll` through ctypes. No third-party dependencies.

![wifiscan のスクリーンショット](docs/screenshot.svg)

<sub>※ 上の画像は実際の画面を模した SVG です。SSID・BSSID はすべて架空のものに置き換えています。</sub>

## なぜ作ったか

`netsh wlan show networks` には、2 つの困った性質があります。

**1. スキャンを実行しない**

netsh はドライバの BSS キャッシュを読むだけです。Windows は接続中にほとんど自発的スキャンをしないため、
キャッシュが痩せて **接続中の 1 件しか返ってこない** ことがあります。Wi-Fi 一覧を開いた直後なら 20 件返るのに、
しばらくすると 1 件になる、という挙動の原因がこれです。

本ツールは `WlanScan()` を明示的に呼んでからビーコンを読むので、常に現在の状態が出ます。

**2. チャネル使用率が読みにくい**

電波が強いのに遅い、という状況の原因はたいてい **チャネルの混雑** です。netsh も BSS Load 自体は出しますが、
`207 (81 %)` のような生値まじりのローカライズされたテキストで、他の情報と並べて比較するのが面倒です。

本ツールは BSS Load 要素を構造体から直接読み、使用率で色分けして表示します。

## 機能

| 機能 | 内容 |
|---|---|
| スキャン | `WlanScan` を実行してから取得。別スレッドなので UI が固まりません |
| 自動更新 | 10〜600 秒間隔 |
| チャネル使用率 | BSS Load 要素から取得し、40% 未満=緑 / 40〜70%=黄 / 70% 以上=赤 で色分け |
| 接続端末数 | BSS Load 要素から取得 |
| RSSI | dBm の生値。netsh の丸められた % より細かく見えます |
| 接続中の強調 | 現在接続している BSSID を太字表示 |
| ステータス行 | 接続中の SSID・BSSID・PHY・受信/送信 Mbps・電波強度 |
| 列ソート | ヘッダクリックで並べ替え |
| ヘッダのツールチップ | 各列の意味と読み方を表示 |
| 2.4GHz の切り替え | ドライバ設定を UI から変更（管理者権限が必要） |
| クリップボードにコピー | 表全体を TSV で。AI に貼って相談しやすい形式です |
| ログ | タイムスタンプつきの操作ログ |

![ツールチップ](docs/tooltip.svg)

## 動作要件

- Windows 10 1703 以降（DPI 対応に `SetProcessDpiAwarenessContext` を使用）
- tkinter 入りの Python 3.8 以降
- 無線 LAN アダプタ

**サードパーティ製ライブラリは不要です。** Pillow も pywin32 も使いません。

## 使い方

```
pythonw.exe wifiscan.pyw
```

`.pyw` なのでコンソールウィンドウは出ません。

> **注意:** Microsoft Store 版 Python のエイリアス（`%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe`）は
> 実体のないスタブのことがあります。その場合は python.org 版か、[uv](https://github.com/astral-sh/uv) が
> 管理する Python を使ってください。
>
> ```
> %APPDATA%\uv\python\cpython-3.12.*-windows-x86_64-none\pythonw.exe
> ```

ダブルクリックで起動したい場合は、`.pyw` を上記 `pythonw.exe` に関連付けてください。
`.pyw` の関連付けは Python インストーラを使っていない環境では未設定のことがあります。

## 列の意味

| 列 | 内容 |
|---|---|
| SSID | ネットワーク名。伏せている AP は `(非公開)` |
| BSSID | AP の無線ごとの MAC アドレス。同じ SSID でもバンドごとに別の行になります |
| BAND | 2.4GHz / 5GHz / 6GHz。中心周波数から算出 |
| CH | チャネル番号。中心周波数から算出 |
| PHY | 11a/b/g/n/ac/ax/be |
| SIGNAL | 信号品質 0〜100%。Windows が RSSI から算出した値 |
| RSSI | 受信信号強度 (dBm)。-50 前後で非常に良好、-80 以下は速度が出にくい |
| STA | その BSSID に接続しているクライアント台数 |
| UTIL | チャネル使用率 |

### UTIL の読み方

AP がそのチャネルを「使用中」と判定した時間の割合です。規格上の定義は
「物理または仮想キャリアセンスで媒体がビジーと判定された時間の割合を 255 に正規化した値」で、
**自分のネットワーク以外の電波も含みます。**

そのため次のようなことが起こります。

- **STA が 0 でも UTIL が高い** — 同じチャネルを使う近隣の AP が混んでいる
- **STA が多くても UTIL が低い** — 台数は多いが、ほとんど通信していない

| UTIL | 状態 |
|---|---|
| 〜30% | 余裕あり |
| 30〜50% | やや混雑 |
| 50〜70% | 競合発生。遅延がばらつく |
| 70%〜 | 飽和。リンク速度が高くても実効速度が出ない |

`STA` と `UTIL` は AP が広告する **BSS Load 要素（Element ID 11）** から取得しています。
この要素はオプションなので、広告していない AP では `-` になります。家庭用ルーターでは省略されがちです。

また、どちらも AP の自己申告値で、直近のビーコン区間の測定値です。瞬間的に振れるため、
1 回の値で判断せず数回見ることをおすすめします。

## 2.4GHz の切り替えについて

ツールバーの `2.4GHz を無効化` / `2.4GHz を有効化` は、ドライバの `WirelessMode` を書き換えます。

- `17` … 5GHz のみ (802.11a)
- `34` … デュアルバンド (802.11a/b/g)

2.4GHz が混雑しているときに、クライアントを強制的に 5GHz へ寄せる用途を想定しています。
Intel のドライバには「優先バンド」設定もありますが、あれは助言レベルで、2.4GHz のほうが電波が強いと
そちらが選ばれ続けます。`WirelessMode` なら物理的に選べなくなります。

**注意点:**

- 変更には管理者権限が必要です。ボタンを押すと UAC の確認が出ます
- アダプタが再起動するため、Wi-Fi が一瞬切断されます
- 無効化すると **2.4GHz 専用の SSID には接続できなくなります**（自宅のルーター、ゲスト用 SSID など）
- `WirelessMode` は Intel 系ドライバのプロパティです。持たないアダプタでは `2.4GHz: 不明` と表示され、
  ボタンは無効になります

状態の読み取りはレジストリを直接見るだけなので、管理者権限は不要です。

## 仕組み

使用している Windows API は 5 つです。

| API | 役割 |
|---|---|
| `WlanOpenHandle` | ハンドル取得 |
| `WlanEnumInterfaces` | インターフェース GUID の取得 |
| `WlanScan` | **スキャンの実行**（netsh にないのがこれ） |
| `WlanGetNetworkBssList` | BSS 一覧を `WLAN_BSS_ENTRY` 構造体で取得 |
| `WlanQueryInterface` | 現在の接続情報（BSSID、送受信レート）を取得 |

netsh のローカライズされたテキストをパースしないので、文字コードや表記ゆれの影響を受けません
（netsh の日本語出力は「チャネル」と「チャンネル」が混在しており、リダイレクトの有無で UTF-8 と CP932 が変わります）。

その他の実装メモ:

- **アイコン** — Pillow を使わず、円弧を数式で描いて無圧縮 DIB の `.ico` を生成します。
  初回のみ生成し、`%LOCALAPPDATA%\wifiscan\wifiscan.ico` にキャッシュします。
  ICO の PNG 圧縮エントリは読み手の対応が割れる（GDI+ が読めない）ため DIB を使っています
- **DPI** — `SetProcessDpiAwarenessContext(PerMonitorV2)` を `Tk()` の生成前に呼び、
  `tk scaling` とピクセル指定値を実 DPI に合わせます。これをしないと高 DPI 環境で文字がぼやけます
- **タスクバー** — `SetCurrentProcessExplicitAppUserModelID` を設定しないと、
  `pythonw.exe` として他の Python アプリとまとめられ、アイコンが反映されません

## 既知の制限

- Windows 専用です
- チャネル幅（20/40/80/160MHz）は表示しません。HT/VHT/HE Operation IE のパースが必要なため
- `WlanScan` は非同期で完了通知が返りますが、本ツールは簡略化のため固定 5 秒待っています
- 6GHz のチャネル番号は `(中心周波数MHz - 5950) / 5` で算出しています

## ライセンス

MIT License. 詳細は [LICENSE](LICENSE) を参照してください。
