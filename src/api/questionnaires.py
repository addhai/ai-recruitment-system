from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from src.models.database import get_db, Questionnaire, QuestionnaireResponse
from src.models.schemas import QuestionnaireCreate, QuestionnaireResponseCreate, QuestionnaireResponseResponse
from src.api.auth import get_current_user, require_hr_admin
from src.safety import InputGuard

router = APIRouter(prefix="/questionnaires", tags=["questionnaires"])


async def _resume_workflow_after_questionnaire(candidate_id: int, questionnaire_id: int, responses: Dict[str, Any]):
    """问卷作答提交后自动恢复工作流：AI 评分并判定是否进入面试环节"""
    try:
        from src.workflow.runner import resume_workflow
        await resume_workflow(candidate_id, "await_questionnaire", {
            "questionnaire_id": questionnaire_id,
            "responses": responses,
        })
    except Exception as e:
        print(f"[questionnaires] 问卷提交后自动恢复工作流失败: {e}")


@router.get("/", response_model=List[dict])
def list_questionnaires(db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    questionnaires = db.query(Questionnaire).all()
    return [
        {
            "id": q.id,
            "name": q.name,
            "type": q.type,
            "question_count": len(q.questions) if isinstance(q.questions, list) else len(q.questions.get("questions", [])),
            "created_at": q.created_at
        }
        for q in questionnaires
    ]


@router.get("/{questionnaire_id}")
def get_questionnaire(questionnaire_id: int, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    questionnaire = db.query(Questionnaire).filter(Questionnaire.id == questionnaire_id).first()
    if not questionnaire:
        raise HTTPException(status_code=404, detail="Questionnaire not found")
    return {
        "id": questionnaire.id,
        "name": questionnaire.name,
        "type": questionnaire.type,
        "questions": questionnaire.questions,
        "created_at": questionnaire.created_at
    }


@router.post("/")
def create_questionnaire(questionnaire: QuestionnaireCreate, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    new_questionnaire = Questionnaire(
        name=questionnaire.name,
        type=questionnaire.type,
        questions=questionnaire.questions,
        created_by=current_user.id
    )
    db.add(new_questionnaire)
    db.commit()
    db.refresh(new_questionnaire)
    return {
        "id": new_questionnaire.id,
        "name": new_questionnaire.name,
        "type": new_questionnaire.type,
        "questions": new_questionnaire.questions
    }


@router.put("/{questionnaire_id}")
def update_questionnaire(questionnaire_id: int, name: str = None, questions: List[dict] = None, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    q = db.query(Questionnaire).filter(Questionnaire.id == questionnaire_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Questionnaire not found")
    
    if name:
        q.name = name
    if questions:
        q.questions = questions
    
    db.commit()
    db.refresh(q)
    return {
        "id": q.id,
        "name": q.name,
        "type": q.type,
        "questions": q.questions
    }


@router.delete("/{questionnaire_id}")
def delete_questionnaire(questionnaire_id: int, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    q = db.query(Questionnaire).filter(Questionnaire.id == questionnaire_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Questionnaire not found")
    db.delete(q)
    db.commit()
    return {"message": "Questionnaire deleted"}


@router.post("/{questionnaire_id}/responses", response_model=QuestionnaireResponseResponse)
async def submit_response(
    questionnaire_id: int,
    response: QuestionnaireResponseCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(require_hr_admin)
):
    """提交问卷作答。提交后自动恢复招聘工作流：AI 评分，达标则自动进入面试排期"""
    q = db.query(Questionnaire).filter(Questionnaire.id == questionnaire_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Questionnaire not found")

    # 答案会进入 AI 评分节点，先过输入护栏拦截提示注入
    is_safe, reason = InputGuard.check_dict_values(response.responses)
    if not is_safe:
        raise HTTPException(status_code=400, detail=f"问卷答案未通过安全检查：{reason}")

    new_response = QuestionnaireResponse(
        candidate_id=response.candidate_id,
        questionnaire_id=questionnaire_id,
        responses=response.responses
    )
    db.add(new_response)
    db.commit()
    db.refresh(new_response)

    # 自动恢复工作流（评分由工作流节点完成并回写本记录的 score 字段）
    background_tasks.add_task(
        _resume_workflow_after_questionnaire,
        response.candidate_id, questionnaire_id, response.responses,
    )
    return new_response


@router.get("/{questionnaire_id}/responses", response_model=List[QuestionnaireResponseResponse])
def get_responses(questionnaire_id: int, db: Session = Depends(get_db), current_user=Depends(require_hr_admin)):
    responses = db.query(QuestionnaireResponse).filter(QuestionnaireResponse.questionnaire_id == questionnaire_id).all()
    return responses
