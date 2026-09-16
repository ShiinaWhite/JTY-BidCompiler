"""产物生成层：把结构化结论变成公司人员能直接用的表格。

两张表的目标不同，不要混：
* **投标要求矩阵** —— "什么必须做、现在有没有、还缺什么"，逐条要求一行；
* **缺失材料清单** —— 只列**真正未决**的项（公司/用户要动手的），不含编译器自己的活。
"""

from .xlsx_matrix import write_requirements_matrix  # noqa: F401
from .xlsx_gaps import write_missing_materials  # noqa: F401
