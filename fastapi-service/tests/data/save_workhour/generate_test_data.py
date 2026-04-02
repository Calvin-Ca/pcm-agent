import json
import random
from datetime import datetime, timedelta

# Configuration
current_date = datetime(2026, 4, 1)
projects = ["AI平台", "智慧园区", "数字化转型", "ERP升级", "移动端改版", "云迁移项目"]
users = ["何思思", "李明", "王芳", "张总", "刘工", "陈经理", "赵丽", "孙涛", "周建国", "吴晓燕"]

def generate_date_expr(days_offset, style="具体"):
    """Generate date expression. style: 具体/今天/明天/昨天/本周/上周"""
    d = current_date + timedelta(days=days_offset)
    if style == "具体":
        return d.strftime("%Y-%m-%d")
    elif style == "今天":
        return "今天"
    elif style == "明天":
        return "明天"
    elif style == "昨天":
        return "昨天"
    elif style == "后天":
        return "后天"
    elif style == "前天":
        return "前天"
    elif style == "大前天":
        return "大前天"
    elif style == "大后天":
        return "大后天"
    return d.strftime("%Y-%m-%d")

def get_entity_type():
    r = random.random()
    if r < 0.7:
        return "employee"
    elif r < 0.9:
        return "deptAdmin"
    else:
        return "companyAdmin"

def generate_user_context(idx):
    user = users[idx % len(users)]
    return {
        "entity_type": get_entity_type(),
        "user_id": f"{1001 + idx % 50}",
        "user_name": user,
        "department_id": f"dept_{str((idx % 10) + 1).zfill(2)}"
    }

def is_date_relative(date_str):
    return date_str in ["今天", "明天", "昨天", "后天", "前天", "大前天", "大后天", "本周", "上周"]

# Subtype distribution
subtypes = {
    "fill_all_params": 50,
    "fill_with_description": 50,
    "fill_missing_project": 30,
    "fill_missing_duration": 30,
    "fill_missing_date": 30,
    "fill_missing_two": 30,
    "fill_project_by_name": 30
}

data = []
idx = 1

# Templates for fill_all_params (project + date + duration)
fill_all_templates = [
    "填报{project}今天{duration}小时",
    "我要填报{project}的工时，今天{duration}小时",
    "帮我在{project}填{duration}小时",
    "{project}今天{duration}小时",
    "今天{project}工作{duration}小时，帮我记录",
    "填报工时：{project}今天{duration}h",
    "麻烦记一下，今天在{project}干了{duration}小时",
    "在{project}项目上今天工作了{duration}小时",
    "今天{project}的工时是{duration}小时",
    "帮我把{project}今天{duration}小时的工时录入",
]

fill_all_long_templates = [
    "我想把这周在{project}的工作记录一下，今天大概是{duration}小时左右",
    "帮我填一下工时，今天在{project}项目上工作了{duration}个小时",
    "请帮我记录今天的工时，是在{project}项目上，花了{duration}小时",
    "我在{project}今天的工作量大概是{duration}小时，帮我录入一下",
    "今天在{project}项目上投入了{duration}小时的工作时间，麻烦录入系统",
]

# Templates for fill_with_description (project + date + duration + description)
fill_desc_templates = [
    "{project}今天{duration}小时，完成了接口文档",
    "填报{project}今天{duration}h，主要是模块设计",
    "今天在{project}干了{duration}小时，处理了几个bug",
    "{project}今天{duration}小时，做了需求分析",
    "帮我在{project}填{duration}小时，工时说明：代码评审",
    "今天{project}项目{duration}小时，完成了周报",
    "{project}今天{duration}h，主要是bug修复",
    "今天{project}的工作{duration}小时，完成了测试用例编写",
    "填报{project}工时，今天{duration}小时，备注：版本发布",
    "在{project}今天{duration}小时，做了性能优化",
]

fill_desc_long_templates = [
    "我在{project}今天投入了大约{duration}小时，主要完成了新功能的设计和实现工作",
    "今天在{project}项目上工作了{duration}个小时，内容包括代码编写和单元测试",
    "请帮我记录今天的工时，是在{project}项目上，大概{duration}小时，主要做了数据库设计",
    "今天在{project}项目上投入了{duration}小时左右，主要完成了接口开发和文档编写",
    "我在{project}今天工作了{duration}小时，主要是系统架构的优化和重构",
]

# Templates for fill_missing_project (date + duration only)
fill_miss_proj_templates = [
    "今天{duration}小时",
    "填报今天{duration}h",
    "帮记录今天{duration}小时工时",
    "我今天工作了{duration}小时",
    "今天工时{duration}小时",
    "填今天{duration}",
    "今天{duration}h",
    "录入今天{duration}小时",
    "记一下今天{duration}工时",
    "工时今天{duration}小时",
]

fill_miss_proj_long_templates = [
    "我想填报今天的工时，大概是{duration}小时",
    "帮我把今天的工时录入一下，今天工作了{duration}个小时",
    "今天的工作时间大概是{duration}小时，帮我记录一下",
    "我在公司工作了一整天，大概花了{duration}小时处理各种事务",
    "请帮我记录今天的工时，今天的投入时间大约是{duration}小时",
]

# Templates for fill_missing_duration (project + date only)
fill_miss_dur_templates = [
    "{project}今天",
    "填报{project}今天工时",
    "帮我在{project}记今天",
    "今天{project}工作",
    "{project}今天多少小时",
    "在{project}今天干了多久",
    "今天{project}怎么填",
    "{project}今天需要填吗",
    "查下{project}今天工时",
    "看看{project}今天情况",
]

fill_miss_dur_long_templates = [
    "我在{project}今天工作了，想填一下工时但是不确定多少小时",
    "今天在{project}项目上工作了一整天，大概多长时间来着",
    "请帮我看看{project}今天的工时怎么录入，我工作了一整天",
    "今天在{project}项目上投入了不少时间，想记录一下但是忘了多久",
    "我在{project}今天的工作情况想记录一下，但是忘记了具体工时",
]

# Templates for fill_missing_date (project + duration only)
fill_miss_date_templates = [
    "{project}{duration}小时",
    "填报{project}{duration}h",
    "{project}工时{duration}小时",
    "帮在{project}填{duration}",
    "{project}项目{duration}小时",
    "这个项目{project}填{duration}",
    "{project}干了{duration}小时",
    "{project}工作{duration}小时",
    "在{project}花了{duration}h",
    "{project}{duration}h怎么填",
]

fill_miss_date_long_templates = [
    "在{project}项目上工作了大约{duration}小时，想录入系统",
    "请帮我记录在{project}项目上的工时，大约{duration}小时",
    "我在{project}这个项目投入了{duration}小时的工作量",
    "{project}项目的工时想录入一下，大概是{duration}小时",
    "我在{project}项目上花了{duration}小时左右，想记录下来",
]

# Templates for fill_missing_two (only one param)
fill_miss_two_proj_templates = [
    "今天8小时",
    "填报今天",
    "工时8小时",
    "今天工作",
    "工作8小时",
    "填工时",
    "工时记录",
    "今天干活",
    "记工时",
    "填报",
]

fill_miss_two_date_templates = [
    "{project}8小时",
    "{project}填报",
    "{project}工时",
    "项目工作",
    "在项目",
    "填{project}",
    "{project}录入",
    "{project}记录",
    "这个项目",
    "{project}怎么填",
]

fill_miss_two_dur_templates = [
    "{project}今天",
    "{project}日期",
    "填报{project}",
    "今天在{project}",
    "项目{project}今天",
    "{project}时间",
    "什么时候",
    "几点",
    "工作时长",
    "多久",
]

# Templates for fill_project_by_name (project name variations)
fill_name_templates = [
    "填报AI平台今天8小时",
    "我要填智慧园区今天8小时",
    "帮我在数字化转型填8小时",
    "AI平台今天8小时",
    "今天智慧园区8小时",
    "ERP升级今天填报8h",
    "移动端改版今天8小时",
    "今天在云迁移项目8小时",
    "填报云迁移8小时",
    "今天AI平台工作8小时",
]

fill_name_long_templates = [
    "我想填报在AI平台项目上今天的工时，大概8小时",
    "今天在智慧园区项目上投入了大约8小时的工作时间",
    "请帮我记录在数字化转型项目上今天的工时情况",
    "我在ERP升级项目上今天工作了8个小时左右",
    "移动端改版这个项目今天的工作量大概是8小时",
]

random.seed(42)

for sub_type, count in subtypes.items():
    for i in range(count):
        item_id = f"swh_{str(idx).zfill(3)}"
        user_ctx = generate_user_context(idx - 1)

        # Determine expression style: 20% short, 20% extra long, 60% normal
        expr_style = random.choices(["short", "normal", "long"], weights=[0.2, 0.6, 0.2])[0]
        colloquial = random.random() < 0.4  # 40% colloquial

        colloquial_words = ["帮我", "麻烦", "记一下", "填一下", "看一下", "瞅一眼", "看看", "查下"]

        if sub_type == "fill_all_params":
            project = random.choice(projects)
            duration = random.choice([6, 7, 8, 9, 10, 4, 5])
            date_expr = random.choice(["今天", "具体日期"])
            if date_expr == "具体日期":
                days_offset = random.randint(-7, 7)
                date_str = generate_date_expr(days_offset, "具体")
            else:
                date_str = "今天"

            if expr_style == "short":
                template = random.choice(fill_all_templates[:5])
                inp = template.format(project=project, duration=duration)
            else:
                template = random.choice(fill_all_templates[5:] + fill_all_long_templates)
                inp = template.format(project=project, duration=duration)

            if colloquial and random.random() < 0.5:
                inp = random.choice(colloquial_words) + inp

            expected = {
                "intent": "tool_execution",
                "tool_name": "save_workhour",
                "params": {
                    "project_id": "通过param_resolver解析",
                    "work_date": date_str if date_str != "今天" else "2026-04-01",
                    "work_hours": duration
                },
                "params_fuzzy": [],
                "params_exists": ["project_id", "date", "duration"],
                "date_relative": is_date_relative(date_str)
            }
            description = f"{sub_type}: {project} {date_str} {duration}小时"

        elif sub_type == "fill_with_description":
            project = random.choice(projects)
            duration = random.choice([6, 7, 8, 9, 10, 4, 5])
            date_str = "今天"
            desc = random.choice(["接口文档", "模块设计", "bug修复", "需求分析", "代码评审", "周报", "版本发布", "性能优化", "测试用例", "数据库设计"])

            if expr_style == "short":
                template = random.choice(fill_desc_templates[:5])
            else:
                template = random.choice(fill_desc_templates[5:] + fill_desc_long_templates)

            inp = template.format(project=project, duration=duration, desc=desc)
            if colloquial and random.random() < 0.5:
                inp = random.choice(colloquial_words) + inp

            expected = {
                "intent": "tool_execution",
                "tool_name": "save_workhour",
                "params": {
                    "project_id": "通过param_resolver解析",
                    "work_date": "2026-04-01",
                    "work_hours": duration,
                    "description": desc
                },
                "params_fuzzy": [],
                "params_exists": ["project_id", "date", "duration", "description"],
                "date_relative": True
            }
            description = f"{sub_type}: {project} {duration}小时 {desc}"

        elif sub_type == "fill_missing_project":
            duration = random.choice([6, 7, 8, 9, 10, 4, 5])
            date_str = "今天"

            if expr_style == "short":
                template = random.choice(fill_miss_proj_templates)
            else:
                template = random.choice(fill_miss_proj_templates[5:] + fill_miss_proj_long_templates)

            inp = template.format(duration=duration)
            if colloquial and random.random() < 0.5:
                inp = random.choice(colloquial_words) + inp

            expected = {
                "intent": "clarify",
                "tool_name": None,
                "params": {},
                "params_fuzzy": [],
                "params_exists": ["date", "duration"],
                "date_relative": True
            }
            description = f"{sub_type}: 缺项目 {date_str} {duration}小时"

        elif sub_type == "fill_missing_duration":
            project = random.choice(projects)
            date_str = "今天"

            if expr_style == "short":
                template = random.choice(fill_miss_dur_templates)
            else:
                template = random.choice(fill_miss_dur_templates[5:] + fill_miss_dur_long_templates)

            inp = template.format(project=project)
            if colloquial and random.random() < 0.5:
                inp = random.choice(colloquial_words) + inp

            expected = {
                "intent": "clarify",
                "tool_name": None,
                "params": {},
                "params_fuzzy": [],
                "params_exists": ["project_id", "date"],
                "date_relative": True
            }
            description = f"{sub_type}: 缺时长 {project} {date_str}"

        elif sub_type == "fill_missing_date":
            project = random.choice(projects)
            duration = random.choice([6, 7, 8, 9, 10, 4, 5])

            if expr_style == "short":
                template = random.choice(fill_miss_date_templates)
            else:
                template = random.choice(fill_miss_date_templates[5:] + fill_miss_date_long_templates)

            inp = template.format(project=project, duration=duration)
            if colloquial and random.random() < 0.5:
                inp = random.choice(colloquial_words) + inp

            expected = {
                "intent": "clarify",
                "tool_name": None,
                "params": {},
                "params_fuzzy": [],
                "params_exists": ["project_id", "duration"],
                "date_relative": False
            }
            description = f"{sub_type}: 缺日期 {project} {duration}小时"

        elif sub_type == "fill_missing_two":
            # Randomly choose which one is missing
            missing_combo = random.choice(["only_date", "only_project", "only_duration"])

            if missing_combo == "only_date":
                duration = random.choice([6, 7, 8])
                if expr_style == "short":
                    template = random.choice(fill_miss_two_proj_templates)
                else:
                    template = random.choice(fill_miss_two_proj_templates[5:] if len(fill_miss_two_proj_templates) > 5 else fill_miss_two_proj_templates)
                inp = template.format(duration=duration)
                expected = {
                    "intent": "clarify",
                    "tool_name": None,
                    "params": {},
                    "params_fuzzy": [],
                    "params_exists": ["duration"],
                    "date_relative": False
                }
                description = f"{sub_type}: 只有时长 {duration}小时"
            elif missing_combo == "only_project":
                duration = random.choice([6, 7, 8])
                if expr_style == "short":
                    template = random.choice(fill_miss_two_date_templates)
                else:
                    template = random.choice(fill_miss_two_date_templates[5:] if len(fill_miss_two_date_templates) > 5 else fill_miss_two_date_templates)
                project = random.choice(projects)
                inp = template.format(project=project, duration=duration)
                expected = {
                    "intent": "clarify",
                    "tool_name": None,
                    "params": {},
                    "params_fuzzy": [],
                    "params_exists": ["date"],
                    "date_relative": True
                }
                description = f"{sub_type}: 只有日期"
            else:  # only_duration
                project = random.choice(projects)
                if expr_style == "short":
                    template = random.choice(fill_miss_two_dur_templates)
                else:
                    template = random.choice(fill_miss_two_dur_templates[5:] if len(fill_miss_two_dur_templates) > 5 else fill_miss_two_dur_templates)
                inp = template.format(project=project)
                expected = {
                    "intent": "clarify",
                    "tool_name": None,
                    "params": {},
                    "params_fuzzy": [],
                    "params_exists": ["project_id"],
                    "date_relative": False
                }
                description = f"{sub_type}: 只有项目 {project}"

        elif sub_type == "fill_project_by_name":
            project = random.choice(projects)
            duration = random.choice([6, 7, 8, 9, 10])
            date_str = "今天"

            if expr_style == "short":
                template = random.choice(fill_name_templates)
            else:
                template = random.choice(fill_name_templates[5:] + fill_name_long_templates)

            inp = template.replace("AI平台", project).replace("智慧园区", project).replace("数字化转型", project).replace("ERP升级", project).replace("移动端改版", project).replace("云迁移项目", project)
            if colloquial and random.random() < 0.5:
                inp = random.choice(colloquial_words) + inp

            expected = {
                "intent": "tool_execution",
                "tool_name": "save_workhour",
                "params": {
                    "project_id": "通过param_resolver解析",
                    "work_date": "2026-04-01",
                    "work_hours": duration
                },
                "params_fuzzy": [],
                "params_exists": ["project_id", "date", "duration"],
                "date_relative": True
            }
            description = f"{sub_type}: 项目名 {project} {duration}小时"

        entry = {
            "id": item_id,
            "category": "save_workhour",
            "sub_type": sub_type,
            "description": description,
            "input": inp,
            "user_context": user_ctx,
            "expected": expected,
            "notes": ""
        }
        data.append(entry)
        idx += 1

# Save to file
output_path = "save_workhour_remaining.json"
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Generated {len(data)} test cases")
print(f"Saved to: {output_path}")

# Verify counts
counts = {}
for item in data:
    counts[item['sub_type']] = counts.get(item['sub_type'], 0) + 1
print("Subtype counts:", counts)