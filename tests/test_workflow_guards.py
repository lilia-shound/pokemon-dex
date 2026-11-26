"""GitHub Actions workflow 的守卫测试（2026-11-24 加）。

**背景**：`.github/workflows/daily-award.yml` 原来带 `schedule: cron: "17 */3 * * *"`，
每 3 小时自动跑一次。它踩了三个坑：

1. Actions 里没有 `WEREAD_API_KEY` ⇒ award 步骤走守卫 `exit 0` 跳过了，
   **但下一步「同步状态并发布图鉴页」照样跑** ⇒ 每 3 小时给姜来发一封失败邮件；
2. 那一步里有 `cp data/pokedex.json site/pokedex.json`
   ⇒ 把瘦身后的图鉴（419KB）**覆盖回完整版（1.8MB）**；
3. 那一步还会 `git push origin HEAD:main` ⇒ **它成了 main 的第二个写者**，
   而 `git subtree push --prefix site origin gh-pages` 跟
   `scripts/deploy.sh` 的 orphan + force 重建方式冲突 ⇒ 必失败。

⇒ 修法：删掉定时触发（只留 `workflow_dispatch`），发布改走 `scripts/deploy.sh`。

**这些断言就是守门用的** —— 以后谁再把定时触发加回来、或者再自己推分支，
这里会立刻红。
"""

from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "daily-award.yml"
TESTS_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
DEPLOY = ROOT / "scripts" / "deploy.sh"


class WorkflowGuardTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        # ★ 断言要看的是**真正执行的代码**，不是注释 ——
        #   这个文件顶部的说明里就写着「schedule」「git subtree push」这些字样，
        #   不剥掉注释的话每条断言都会误报。
        self.code = "\n".join(
            line for line in self.text.split("\n")
            if not line.strip().startswith("#")
        )

    def test_the_workflow_file_exists(self):
        self.assertTrue(WORKFLOW.exists())

    def test_there_is_no_scheduled_trigger(self):
        """★ 定时触发必须没有 —— 它每 3 小时失败一次，还给姜来发邮件。"""
        self.assertNotIn("schedule:", self.code)
        self.assertNotIn("cron:", self.code)

    def test_only_manual_dispatch_remains(self):
        self.assertIn("workflow_dispatch:", self.code)

    def test_it_does_not_push_branches_itself(self):
        """★ 它不能自己推 main / gh-pages —— 那会跟 Mavis cron 抢着写。"""
        self.assertNotIn("git push origin", self.code)
        self.assertNotIn("git subtree push", self.code)

    def test_it_does_not_overwrite_the_slim_pokedex(self):
        """★ 不能用完整版覆盖瘦身版 —— 那会让图鉴页的 pokedex.json 从 419KB 变 1.8MB。"""
        self.assertNotIn("cp data/pokedex.json site/pokedex.json", self.code)

    def test_publishing_goes_through_the_deploy_script(self):
        """发布只走 scripts/deploy.sh（唯一的发布路径）。"""
        self.assertIn("scripts/deploy.sh", self.code)

    def test_it_still_skips_gracefully_without_the_secret(self):
        """没配 key 时要 exit 0（成功），不能 exit 1（失败邮件）。"""
        self.assertIn('if [ -z "$WEREAD_API_KEY" ]; then', self.code)
        self.assertIn("exit 0", self.code)

    def test_the_deploy_script_uses_the_token_from_the_environment(self):
        """deploy.sh 吃环境里的 GITHUB_TOKEN ⇒ workflow 必须把它传进去。"""
        deploy = DEPLOY.read_text(encoding="utf-8")
        self.assertIn("GITHUB_TOKEN", deploy)
        self.assertIn("secrets.GITHUB_TOKEN", self.code)


class TestsWorkflowGuardTest(unittest.TestCase):
    """`.github/workflows/tests.yml` 的守卫：**只跑测试，什么都不改**。

    ★ 为什么要单独守：
      2026-11-24 出过事 —— 一个带 `schedule:` 的 workflow 成了 main 的**第二个写者**
      （它自己 `git push`），还把瘦身后的图鉴覆盖回完整版、每 3 小时给姜来发一封失败邮件。
      ⇒ 这个「只跑测试」的 workflow 也得守住同样的纪律：
        **不定时触发、不推分支、不碰发布**。
    """

    def setUp(self):
        self.assertTrue(TESTS_WORKFLOW.exists(), "测试 workflow 不见了")
        self.text = TESTS_WORKFLOW.read_text(encoding="utf-8")
        # 断言要看的是**真正执行的代码**，不是注释
        self.code = "\n".join(
            line for line in self.text.split("\n")
            if not line.strip().startswith("#")
        )

    def test_it_runs_the_test_suite(self):
        """它得真的把整套测试跑起来（不然就是个空 workflow）。"""
        self.assertIn("run_tests.py", self.code)

    def test_it_has_no_scheduled_trigger(self):
        """★ 不许定时触发 —— 没人看着的时候自己跑，出问题只会变成噪音。"""
        self.assertNotIn("schedule:", self.code)
        self.assertNotIn("cron:", self.code)

    def test_it_does_not_push_anything(self):
        """★ 它不能推分支 —— 那是 Mavis cron / deploy.sh 的活。"""
        self.assertNotIn("git push", self.code)
        self.assertNotIn("git subtree push", self.code)

    def test_it_does_not_publish_the_site(self):
        """发布只走 scripts/deploy.sh（唯一的发布路径）。"""
        self.assertNotIn("scripts/deploy.sh", self.code)
        self.assertNotIn("gh-pages", self.code)

    def test_it_can_also_be_run_by_hand(self):
        """能手动跑一次（排查问题时用）。"""
        self.assertIn("workflow_dispatch:", self.code)

    def test_it_installs_the_browser_the_browser_tests_need(self):
        """浏览器层的断言要 chromium —— 少装一步，那些用例会静默跳过（等于没测）。"""
        self.assertIn("playwright install", self.code)


if __name__ == "__main__":
    unittest.main()
