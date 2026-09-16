@echo off
rem bid-ai-clean 增量登记刷新（供 Windows 计划任务调用；手动跑也可双击本文件）
rem 首行 chcp 65001：保证中文正常显示（计划任务里乱码会让排查更慢）
rem
rem 手动想先预览（不写库）：把下面那行命令加上 --dry-run 再跑：
rem   "...\refresh_catalog.py" --dry-run
rem
rem 首次建库（若 reg 库不存在）：额外加 --init-db。
rem 退出码 0=成功；2=根不可访问 / 禁写库 / 库缺表（schtasks 结果里可见）。
chcp 65001 >nul
set "BID_AI_CLEAN_DB=C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean\bid_ai_clean_reg.db"
"C:\Users\hao.guo\AppData\Local\miniconda3\envs\langchain-dev\python.exe" ^
  "C:\Users\hao.guo\Desktop\标书文库\bid-ai-clean\scripts\refresh_catalog.py"