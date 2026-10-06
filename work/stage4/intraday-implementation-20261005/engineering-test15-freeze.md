# test15 冻结工程核验

实际服务器日志：532/532，28.783 秒，OK，无 skip。独立逐行核实新增三组为 decision_reply_identity 8、predecision_acceptance 18、decision_confirmation_acceptance 7，全部 ok。

source-manifest.json 189 项、source.zip 189 个文件及当前 L 文件逐一 SHA256 相符，无缺失或漂移。相对 test13 的 184 文件共10个差异路径：3业务源、4验收脚本、3测试文件（其中5新文件）；无提示词变化。详细路径和核验机器结果见 engineering-test15-freeze.json。

业务修复将已接纳 delivery 的决定确认与原登记 partial 以真实宿主决定身份区分，并在实际dispatch复核phase；保留原pair未知占位、旧确认兼容和同决定重试幂等。新后验验收核source-bound未答等待和非空实际决定确认双账，不读模型文字当成功。

test14 原531回归的3个旧验收夹具 KeyError 保留。test15只把新覆盖检查收紧到必要分支，并将真正缺intent_id明确记未覆盖，旧夹具和业务合同未改。

这是冻结版本及程序回归确认，不是native06或整体4C放行。受影响10案的fresh Flash、独立渠道与责任实链需等闭合新证据核验；旧native05单意图确认失败及第二同目标请求的语义/来源责任问题仍按原证据保留。不会用程序全绿关闭回答内容P1或扩大为4D/4E通过。
