#!/usr/bin/env python3
"""
随机点名软件 Web 服务
使用方法: python random_picker_server.py [端口]
默认端口: 8080
"""

import http.server
import socketserver
import sys
from pathlib import Path

# HTML 内容 - 兼容性更好的版本
HTML_FILE = Path(__file__).with_name('random-picker.html')

HTML_CONTENT = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>随机课堂点名</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }

        .container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 600px;
            width: 100%;
        }

        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 30px;
            font-size: 2em;
        }

        .input-section {
            margin-bottom: 30px;
        }

        label {
            display: block;
            margin-bottom: 10px;
            color: #555;
            font-weight: 600;
        }

        textarea {
            width: 100%;
            height: 120px;
            padding: 15px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 16px;
            resize: vertical;
            transition: border-color 0.3s;
        }

        textarea:focus {
            outline: none;
            border-color: #667eea;
        }

        .hint {
            font-size: 12px;
            color: #999;
            margin-top: 5px;
        }

        .display-section {
            text-align: center;
            margin: 30px 0;
        }

        .result {
            font-size: 4em;
            font-weight: bold;
            color: #667eea;
            min-height: 120px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            border-radius: 15px;
            margin-bottom: 20px;
            transition: all 0.3s;
        }

        .result.rolling {
            animation: pulse 0.1s infinite;
        }

        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.02); }
        }

        .btn {
            width: 100%;
            padding: 18px;
            font-size: 1.3em;
            font-weight: bold;
            color: white;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border: none;
            border-radius: 12px;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4);
        }

        .btn:active {
            transform: translateY(0);
        }

        .btn:disabled {
            opacity: 0.7;
            cursor: not-allowed;
        }

        .stats {
            display: flex;
            justify-content: space-around;
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid #eee;
        }

        .stat-item {
            text-align: center;
        }

        .stat-value {
            font-size: 1.5em;
            font-weight: bold;
            color: #667eea;
        }

        .stat-label {
            font-size: 0.9em;
            color: #999;
        }

        .history {
            margin-top: 20px;
            max-height: 150px;
            overflow-y: auto;
        }

        .history-title {
            font-size: 0.9em;
            color: #666;
            margin-bottom: 10px;
        }

        .history-item {
            display: inline-block;
            padding: 5px 12px;
            margin: 3px;
            background: #f0f0f0;
            border-radius: 20px;
            font-size: 0.9em;
        }

        .confetti {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            overflow: hidden;
            z-index: 1000;
        }

        .confetti-piece {
            position: absolute;
            width: 10px;
            height: 10px;
            animation: fall 3s ease-out forwards;
        }

        @keyframes fall {
            0% {
                transform: translateY(-100px) rotate(0deg);
                opacity: 1;
            }
            100% {
                transform: translateY(100vh) rotate(720deg);
                opacity: 0;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>随机课堂点名</h1>

        <div class="input-section">
            <label for="names">学生名单</label>
            <textarea id="names" placeholder="输入学生姓名，每行一个或逗号分隔&#10;例如：&#10;张三&#10;李四&#10;王五">张三
李四
王五
赵六
钱七</textarea>
            <p class="hint">提示：支持换行或逗号、空格分隔姓名</p>
        </div>

        <div class="display-section">
            <div class="result" id="result">???</div>
            <button class="btn" id="startBtn" type="button">开始点名</button>
        </div>

        <div class="stats">
            <div class="stat-item">
                <div class="stat-value" id="totalCount">0</div>
                <div class="stat-label">总人数</div>
            </div>
            <div class="stat-item">
                <div class="stat-value" id="pickCount">0</div>
                <div class="stat-label">已点名</div>
            </div>
        </div>

        <div class="history" id="historySection" style="display: none;">
            <div class="history-title">点名记录</div>
            <div id="historyList"></div>
        </div>
    </div>

    <div class="confetti" id="confetti"></div>

    <script>
        var isRolling = false;
        var history = [];
        var rollTimer = null;

        // 初始化人数统计
        updateTotalCount();
        document.getElementById('names').addEventListener('input', updateTotalCount);
        document.getElementById('startBtn').addEventListener('click', startPick);

        function parseNames() {
            var input = document.getElementById('names').value;
            var names = input.split(/[\n,，\s]+/);
            var result = [];
            for (var i = 0; i < names.length; i++) {
                if (names[i].trim() !== '') {
                    result.push(names[i].trim());
                }
            }
            return result;
        }

        function updateTotalCount() {
            var names = parseNames();
            document.getElementById('totalCount').textContent = names.length;
        }

        function startPick() {
            if (isRolling) return;

            var names = parseNames();
            if (names.length === 0) {
                alert('请先输入学生名单！');
                return;
            }

            var resultEl = document.getElementById('result');
            var btn = document.getElementById('startBtn');

            isRolling = true;
            btn.disabled = true;
            btn.textContent = '滚动中...';
            resultEl.classList.add('rolling');

            // 滚动动画
            var speed = 50;
            var count = 0;
            var maxCount = Math.floor(30 + Math.random() * 10);

            function rollOnce() {
                var randomName = names[Math.floor(Math.random() * names.length)];
                resultEl.textContent = randomName;
                count++;

                // 逐渐减速
                if (count > maxCount * 0.7) speed = 100;
                if (count > maxCount * 0.9) speed = 200;

                if (count >= maxCount) {
                    rollTimer = null;
                    finishPick(randomName);
                    return;
                }

                rollTimer = setTimeout(rollOnce, speed);
            }

            rollOnce();
        }

        function finishPick(winner) {
            var resultEl = document.getElementById('result');
            var btn = document.getElementById('startBtn');

            resultEl.classList.remove('rolling');
            resultEl.style.transform = 'scale(1.2)';
            setTimeout(function() {
                resultEl.style.transform = 'scale(1)';
            }, 300);

            // 添加到历史记录
            history.push(winner);
            updateHistory();

            // 更新统计
            document.getElementById('pickCount').textContent = history.length;

            // 彩带效果
            createConfetti();

            // 恢复按钮
            isRolling = false;
            btn.disabled = false;
            btn.textContent = '再次点名';
        }

        function updateHistory() {
            var section = document.getElementById('historySection');
            var list = document.getElementById('historyList');

            if (history.length > 0) {
                section.style.display = 'block';
                var html = '';
                for (var i = 0; i < history.length; i++) {
                    html += '<span class="history-item">' + (i + 1) + '. ' + history[i] + '</span>';
                }
                list.innerHTML = html;
            }
        }

        function createConfetti() {
            var colors = ['#667eea', '#764ba2', '#f093fb', '#f5576c', '#4facfe', '#00f2fe'];
            var container = document.getElementById('confetti');

            for (var i = 0; i < 50; i++) {
                var piece = document.createElement('div');
                piece.className = 'confetti-piece';
                piece.style.left = Math.random() * 100 + '%';
                piece.style.backgroundColor = colors[Math.floor(Math.random() * colors.length)];
                piece.style.animationDelay = Math.random() * 0.5 + 's';
                piece.style.animationDuration = (2 + Math.random() * 2) + 's';

                // 随机形状
                var shapes = ['50%', '0%', '50% 0 50% 50%'];
                piece.style.borderRadius = shapes[Math.floor(Math.random() * shapes.length)];

                container.appendChild(piece);

                // 清理
                setTimeout(function(p) {
                    return function() { p.remove(); };
                }(piece), 4000);
            }
        }

        window.startPick = startPick;
    </script>
</body>
</html>
'''


class MyHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(get_html_content().encode('utf-8'))
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}")


def get_html_content():
    if HTML_FILE.exists():
        return HTML_FILE.read_text(encoding='utf-8')

    return HTML_CONTENT


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

    with socketserver.TCPServer(("", port), MyHandler) as httpd:
        print(f"\n随机点名服务已启动！")
        print(f"请访问: http://localhost:{port}")
        print(f"局域网访问: http://{get_local_ip()}:{port}")
        print(f"\n按 Ctrl+C 停止服务\n")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n服务已停止")


def get_local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


if __name__ == '__main__':
    main()
