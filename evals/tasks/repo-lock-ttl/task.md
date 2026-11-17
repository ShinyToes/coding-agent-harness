仓库里有一个回归：进程崩溃后 session 会一直处于「被锁」状态，`h resume` 无法接管，即使调用方把锁的 TTL 设成 0 也不释放。

现象：

- 正常拿到锁、另一个 owner 在 TTL 内被拒绝——这部分是对的
- 但过期锁（TTL 已过）仍然拒绝新 owner，导致崩溃后永远无法恢复

请定位并修复，保持其他锁行为不变（同一 owner 重复获取、release 后释放、TTL 内拒绝其他 owner）。

相关位置：`harness/store.py` 的 `acquire_lock()`。
依赖已装好，改完直接跑 `python -m pytest tests/test_store.py tests/test_hardening.py -q` 验证，通过后调用 `finish`。
