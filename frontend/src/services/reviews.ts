import { apiRequest } from './api';
import type { ReviewItem } from '../types';

export const getPendingReviews = async (): Promise<ReviewItem[]> => {
  return apiRequest<ReviewItem[]>('/reviews/');
};

/**
 * 裁决待复核候选人。
 * 不重跑 AI（服务端直接改写终局状态），因此结果确定、不会二次漂移。
 */
export const decideReview = async (
  candidateId: number,
  decision: 'approve' | 'reject',
  note?: string
): Promise<{ candidate_id: number; status: string; tags: string[] }> => {
  return apiRequest(`/reviews/candidates/${candidateId}`, {
    method: 'POST',
    body: { decision, note },
  });
};