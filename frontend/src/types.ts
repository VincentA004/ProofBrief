export type BriefListItem = {
  briefId: string;
  status: 'PENDING' | 'DONE';
  candidate: { id: string; name: string };
  job: { id: string; title: string };
  createdAt?: string | null;
};

export type BriefDetail = {
  briefId: string;
  status: 'PENDING' | 'DONE';
  candidate: { id: string; name: string };
  job: { id: string; title: string };
  final?: { key: string; url: string };
};

export type CreateBriefResponse = {
  briefId: string;
  uploads: {
    resume: { key: string; putUrl: string };
    jd: { key: string; putUrl: string };
  };
};