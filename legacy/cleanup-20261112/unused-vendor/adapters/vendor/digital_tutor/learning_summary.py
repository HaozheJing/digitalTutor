"""A fixed, family-scoped snapshot aggregation; does not infer mastery."""
from pydantic import Field
from .domain import Strict,ID,require
from .learning import query_learning

ROLES={'service','planner_daily','planner_progress','admin'}


class Summary(Strict):
    batch_id: ID
    start_day: int=Field(default=1,ge=1)
    end_day: int=Field(default=366,ge=1)


def read(app,token,args):
    cmd=Summary.model_validate(args)
    require(cmd.start_day<=cmd.end_day,'INVALID_RANGE','天次范围无效')
    result=query_learning(app,token,{'sql':
        'SELECT completion_status,homework_status,COUNT(*) AS days,MIN(day) AS first_day,MAX(day) AS last_day '
        'FROM learning_days WHERE batch_id=:batch AND day>=:start AND day<=:end '
        'GROUP BY completion_status,homework_status ORDER BY completion_status,homework_status',
        'parameters':{'batch':cmd.batch_id,'start':cmd.start_day,'end':cmd.end_day},'max_rows':100})
    return {**result,'batch_id':cmd.batch_id,'day_range':[cmd.start_day,cmd.end_day],
        'contract':'仅汇总指定批次的原始完课、作业状态，不合并不同批次；空结果代表没有该范围记录，不是未学或0分。参与不证明掌握。课程身份须另查已确认绑定。'}


SPECS={'learning.summary':(ROLES,Summary,'按指定批次和课程天次范围汇总完课及作业原始状态。确定性工具，无需现场编写SQL；不推断学习掌握。')}
HANDLERS={'learning.summary':read}
