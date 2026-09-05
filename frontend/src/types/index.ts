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
  created_at: string;
  updated_at: string;
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
