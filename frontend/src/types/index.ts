export const __TYPES__ = 'types';

export interface User {
  id: number;
  username: string;
  email: string;
  full_name: string | null;
  department: string | null;
  role: string;
  created_at: string;
}

export interface Candidate {
  id: number;
  name: string;
  email: string | null;
  phone: string | null;
  resume_file: string | null;
  status: string;
  source: string | null;
  position: string | null;
  job_description_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface Position {
  id: number;
  title: string;
  department: string | null;
  location: string | null;
  headcount: number | null;
  description: string | null;
  status: 'draft' | 'active' | 'archived';
  jd_count: number;
  active_jd_count: number;
  candidate_count: number;
  created_at: string;
  updated_at: string;
}

export interface JdSkill {
  skill: string;
  evidence: string;
}

export interface JdBasic {
  department?: string;
  location?: string;
  employment_type?: string;
  headcount?: number;
  experience_years_required?: string;
  experience_years_preferred?: string;
  education_required?: string;
  education_preferred?: string;
}

export interface JdProfile {
  basic: JdBasic;
  responsibilities: string[];
  required_skills: JdSkill[];
  preferred_skills: JdSkill[];
  tech_stack: string[];
  domain_knowledge: string[];
  soft_skills: string[];
  culture_values: string[];
  keywords: string[];
}

export interface JobDescription {
  id: number;
  position_id: number | null;
  title: string;
  department: string | null;
  status: 'draft' | 'active' | 'archived';
  parse_status: 'pending' | 'parsed' | 'failed';
  parse_error: string | null;
  raw_text: string | null;
  parsed_data: JdProfile | null;
  candidate_count: number;
  created_at: string;
  updated_at: string;
}

/** 待人工复核的候选人（文化契合落在待复核区间） */
export interface ReviewItem {
  candidate_id: number;
  name: string;
  position: string | null;
  job_description_id: number | null;
  status: string;
  final_decision: string | null;
  overall_score: number | null;
  skill_match_score: number | null;
  experience_match_score: number | null;
  education_match_score: number | null;
  culture_match_score: number | null;
  questionnaire_score: number | null;
  interview_scores: { round: number; score: number }[];
  needs_review: boolean;
  review_reason: string | null;
  review_detail: {
    dimension?: string;
    score?: number;
    pass_threshold?: number;
    review_threshold?: number;
    culture_values?: string[];
    degraded?: boolean;
  } | null;
  assessed_dimensions: string[];
  scoring_version: string | null;
  // 评分口径指纹（由后端代码 + 运行时配置算出）：指纹不同即表示口径变过，
  // 分数不可直接比较；相同也不保证分数可复现（模型采样本身有随机性）
  scoring_fingerprint: string | null;
  talent_pool_tags: string[] | null;
  updated_at: string | null;
}

export interface InterviewQuestion {
  question: string;
  focus: string;
}

export interface Interview {
  id: number;
  candidate_id: number;
  position: string;
  round: number;
  status: string;
  interviewer_id: number | null;
  scheduled_at: string | null;
  completed_at: string | null;
  score: number | null;
  feedback: string | null;
  notes: string | null;
  questions?: InterviewQuestion[] | null;
  created_at: string;
}

export interface Questionnaire {
  id: number;
  name: string;
  type: string;
  questions: any;
  created_at: string;
}

export interface Evaluation {
  id: number;
  candidate_id: number;
  evaluator_id: number;
  dimension: string;
  score: number;
  comment: string | null;
  created_at: string;
}

export interface TalentPoolEntry {
  id: number;
  candidate_id: number;
  status: string;
  tags: string[] | null;
  last_contact: string | null;
  next_contact: string | null;
  created_at: string;
}

export interface DashboardStats {
  total_candidates: number;
  pending_candidates: number;
  interviewed_candidates: number;
  hired_candidates: number;
  avg_interview_time: number;
  avg_match_score: number;
  this_month_candidates: number;
  interview_progress: { status: string; count: number }[];
}

export interface TrendData {
  date: string;
  day: string;
  candidates: number;
  interviews: number;
}

export interface KnowledgeBaseDoc {
  title: string;
  content: string;
}

export interface QueryResult {
  answer: string;
  sources: { title: string; content: string }[];
}
