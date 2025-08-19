import { fetchAuthSession } from 'aws-amplify/auth';
import { config } from './config';
import type { BriefListItem, BriefDetail, CreateBriefResponse } from './types';

const BASE = config.apiBase;

async function authHeaders() {
  try {
    const session = await fetchAuthSession();
    const idToken = session.tokens?.idToken?.toString();
    return idToken ? { Authorization: `Bearer ${idToken}` } : {};
  } catch (error) {
    console.warn('Failed to get auth token:', error);
    return {};
  }
}

export async function createBrief(candidate: string, jobTitle: string): Promise<CreateBriefResponse> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/briefs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify({ 
      candidate: { fullName: candidate }, 
      job: { title: jobTitle } 
    }),
  });
  if (!res.ok) throw new Error(`createBrief failed: ${res.status} ${res.statusText}`);
  return res.json();
}

export async function startBrief(id: string): Promise<{ message: string }> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/briefs/${id}/start`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...headers },
  });
  if (!res.ok) throw new Error(`startBrief failed: ${res.status} ${res.statusText}`);
  return res.json();
}

export async function listBriefs(): Promise<BriefListItem[]> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/briefs`, { headers });
  if (!res.ok) throw new Error(`listBriefs failed: ${res.status} ${res.statusText}`);
  return res.json();
}

export async function getBrief(id: string): Promise<BriefDetail> {
  const headers = await authHeaders();
  const res = await fetch(`${BASE}/briefs/${id}`, { headers });
  if (!res.ok) throw new Error(`getBrief failed: ${res.status} ${res.statusText}`);
  return res.json();
}