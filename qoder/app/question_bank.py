"""内置题库与选题逻辑。

为了让复习助手在**没有大模型 API**的情况下也能开箱即用，这里提供一个确定性的
小型题库，并实现两种选题策略：

- :func:`diagnostic_questions`：学习者无历史时，跨知识点均匀取样做诊断。
- :func:`targeted_questions`：有历史时，优先重练最近错题、再靶向最薄弱知识点，
  最后用其余题目补齐到目标数量。

题库可按需扩展；新增题目只要遵循同一结构即可被选题逻辑自动纳入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Question:
    """题库中的一道题。"""

    id: str
    subject: str
    kp: str  # knowledge point 知识点标签
    prompt: str
    options: tuple[str, ...]
    correct_index: int
    explanation: str = ""


# ---------------------------------------------------------------------- #
# 题库数据
# ---------------------------------------------------------------------- #
def _q(qid: str, subject: str, kp: str, prompt: str, options: Sequence[str], answer: int, why: str) -> Question:
    return Question(qid, subject, kp, prompt, tuple(options), answer, why)


_BANK: tuple[Question, ...] = (
    # ---------------- Python ---------------- #
    _q("py-01", "python", "列表推导式", "下面哪一项能生成 [0, 1, 4]？",
       ["[x**2 for x in range(3)]", "[x*2 for x in range(3)]", "[x**2 for x in range(4)]", "list(range(3))"],
       0, "range(3)=0,1,2，逐项平方得 0,1,4。"),
    _q("py-02", "python", "列表推导式", "列表推导式 [x for x in range(5) if x % 2 == 0] 的结果是？",
       ["[0, 2, 4]", "[1, 3]", "[0, 1, 2, 3, 4]", "[2, 4]"],
       0, "if 过滤保留偶数，range(5) 中偶数为 0、2、4。"),
    _q("py-03", "python", "字典操作", "d = {'a': 1}; d.get('b', 0) 的返回值是？",
       ["None", "0", "1", "抛出 KeyError"],
       1, "get 在键不存在时返回默认值 0，不会抛异常。"),
    _q("py-04", "python", "字典操作", "以下哪个方法会返回字典所有键？",
       ["values()", "items()", "keys()", "get()"],
       2, "keys() 返回全部键的视图。"),
    _q("py-05", "python", "字符串方法", "'a,b,c'.split(',') 的结果是？",
       ["['a,b,c']", "['a', 'b', 'c']", "('a','b','c')", "'abc'"],
       1, "split 按逗号切分返回列表。"),
    _q("py-06", "python", "字符串方法", "f'{2:03d}' 的结果字符串是？",
       ["'2'", "'002'", "'203'", "'003'"],
       1, "格式 03d 表示宽度 3、左侧补零的十进制整数。"),
    _q("py-07", "python", "函数与作用域", "Python 中函数默认参数的求值时机是？",
       ["每次调用时", "定义函数时求值一次", "运行时动态求值", "从不求值"],
       1, "默认值在函数定义时求值一次，故可变默认值是常见陷阱。"),
    _q("py-08", "python", "函数与作用域", "在函数内要修改外层不可变变量，需要哪个关键字？",
       ["global 或 nonlocal", "static", "extern", "const"],
       0, "修改全局用 global，修改嵌套外层用 nonlocal。"),
    _q("py-09", "python", "异常处理", "try/except/finally 中 finally 的执行时机是？",
       ["仅无异常时", "仅有异常时", "无论是否异常都执行", "从不执行"],
       2, "finally 总是执行，常用于释放资源。"),
    _q("py-10", "python", "异常处理", "raise 一个异常时应使用哪种写法从原异常链式抛出？",
       ["raise NewError from exc", "raise NewError(exc)", "raise NewError -> exc", "throw NewError"],
       0, "from 语法保留异常链，便于定位根因。"),
    _q("py-11", "python", "类型注解", "typing.Optional[int] 等价于？",
       ["int | None", "int", "None", "list[int]"],
       0, "Optional[int] 表示 int 或 None。"),
    _q("py-12", "python", "类型注解", "下面哪个是合法的变量注解？",
       ["x: int = 5", "int x = 5", "x = int 5", "let x: int = 5"],
       0, "Python 用 `名字: 类型 = 值` 的注解语法。"),

    # ---------------- 数学 ---------------- #
    _q("math-01", "math", "一元二次方程", "方程 x^2 - 5x + 6 = 0 的两个根是？",
       ["2 和 3", "-2 和 -3", "1 和 6", "-1 和 -6"],
       0, "因式分解为 (x-2)(x-3)=0。"),
    _q("math-02", "math", "一元二次方程", "判别式 Δ = b^2 - 4ac 小于 0 时，实数根个数是？",
       ["0", "1", "2", "无穷多"],
       0, "Δ<0 无实数根（有两个共轭复根）。"),
    _q("math-03", "math", "函数与图像", "y = 2x + 1 的图像斜率是？",
       ["1", "2", "-2", "0.5"],
       1, "一次项系数即斜率 2。"),
    _q("math-04", "math", "函数与图像", "函数 y = x^2 在 x=1 处的导数是？",
       ["1", "2", "0", "x"],
       1, "y' = 2x，代入 x=1 得 2。"),
    _q("math-05", "math", "概率统计", "掷一枚均匀骰子，出现偶数的概率是？",
       ["1/6", "1/3", "1/2", "2/3"],
       2, "偶数为 2、4、6 共 3 个，概率 3/6=1/2。"),
    _q("math-06", "math", "概率统计", "一组数据 2,4,4,6,9 的中位数是？",
       ["4", "5", "6", "4.5"],
       0, "排序后中间位置的数是 4。"),
    _q("math-07", "math", "几何", "半径为 3 的圆面积是（取 π）？",
       ["6π", "9π", "3π", "12π"],
       1, "面积 = πr² = 9π。"),
    _q("math-08", "math", "几何", "直角三角形两直角边为 3 和 4，斜边长是？",
       ["5", "6", "7", "25"],
       0, "勾股定理 √(9+16)=5。"),

    # ---------------- 英语 ---------------- #
    _q("en-01", "english", "时态", "Choose: She ___ to school every day.",
       ["go", "goes", "going", "gone"],
       1, "一般现在时第三人称单数用 goes。"),
    _q("en-02", "english", "时态", "Choose: I ___ my homework already.",
       ["finish", "finished", "have finished", "am finishing"],
       2, "already 常与现在完成时连用。"),
    _q("en-03", "english", "词汇", "The word 'ubiquitous' most nearly means:",
       ["rare", "everywhere", "ancient", "fragile"],
       1, "ubiquitous 意为无处不在的。"),
    _q("en-04", "english", "词汇", "Antonym of 'benevolent':",
       ["kind", "cruel", "brave", "calm"],
       1, "benevolent（仁慈的）的反义是 cruel。"),
    _q("en-05", "english", "介词", "Choose: The book is ___ the table.",
       ["in", "on", "at", "of"],
       1, "表示在……表面上用 on。"),
    _q("en-06", "english", "介词", "Choose: She is good ___ math.",
       ["in", "at", "on", "for"],
       1, "be good at 固定搭配。"),
    _q("en-07", "english", "从句", "Choose: This is the man ___ helped me.",
       ["which", "who", "whom", "whose"],
       1, "指人且作主语用 who。"),
    _q("en-08", "english", "从句", "Choose: I know ___ he is honest.",
       ["what", "that", "which", "who"],
       1, "宾语从句陈述事实用 that 引导。"),
)


def all_subjects() -> list[str]:
    """题库覆盖的学科列表。"""

    seen: dict[str, None] = {}
    for item in _BANK:
        seen.setdefault(item.subject, None)
    return list(seen)


def questions_for_subject(subject: str) -> list[Question]:
    """返回某学科的全部题目。"""

    return [item for item in _BANK if item.subject == subject]


def knowledge_points(subject: str) -> list[str]:
    """返回某学科的知识点标签（保持出现顺序、去重）。"""

    seen: dict[str, None] = {}
    for item in questions_for_subject(subject):
        seen.setdefault(item.kp, None)
    return list(seen)


def by_id(subject: str, question_id: str) -> Question | None:
    for item in questions_for_subject(subject):
        if item.id == question_id:
            return item
    return None


def diagnostic_questions(subject: str, count: int) -> list[Question]:
    """跨知识点轮转取样，得到覆盖尽量广的诊断题。"""

    pool = questions_for_subject(subject)
    if not pool:
        return []
    buckets: dict[str, list[Question]] = {}
    for item in pool:
        buckets.setdefault(item.kp, []).append(item)

    picked: list[Question] = []
    # 逐轮从每个知识点各取一道，直到达到数量或题库耗尽
    idx = 0
    while len(picked) < count:
        progressed = False
        for kp in buckets:
            if idx < len(buckets[kp]):
                picked.append(buckets[kp][idx])
                progressed = True
                if len(picked) >= count:
                    break
        if not progressed:
            break
        idx += 1
    return picked


def targeted_questions(
    subject: str,
    count: int,
    *,
    weak_kps: Iterable[str],
    recent_wrong_ids: Iterable[str],
) -> list[Question]:
    """靶向选题：先重练最近错题，再针对薄弱知识点，最后补齐。

    - ``weak_kps`` 应按薄弱程度从高到低排序。
    - ``recent_wrong_ids`` 为最近答错的题目 ID（新→旧）。
    """

    pool = questions_for_subject(subject)
    if not pool or count <= 0:
        return []

    picked: list[Question] = []
    picked_ids: set[str] = set()

    def add(item: Question) -> None:
        if item.id not in picked_ids and len(picked) < count:
            picked.append(item)
            picked_ids.add(item.id)

    id_to_q = {item.id: item for item in pool}

    # 1) 最近错题重练
    for qid in recent_wrong_ids:
        item = id_to_q.get(qid)
        if item is not None:
            add(item)

    # 2) 靶向薄弱知识点
    for kp in weak_kps:
        for item in pool:
            if item.kp == kp:
                add(item)

    # 3) 用诊断式取样补齐剩余名额
    if len(picked) < count:
        for item in diagnostic_questions(subject, count * 2):
            add(item)
            if len(picked) >= count:
                break

    return picked[:count]
