import { Link, useParams } from 'react-router-dom';
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useToast } from '@/hooks/use-toast';
import { getBrief } from '@/api';
import type { BriefDetail } from '@/types';

import {
  ArrowLeft,
  Download,
  RefreshCw,
  Terminal,
  CheckCircle2,
  AlertTriangle,
  Clipboard,
  ClipboardCheck,
  HelpCircle,
  Link as LinkIcon,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Separator } from '@/components/ui/separator';
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts';
import * as React from 'react';

/* -------------------------------------------------------------------------- */
/*                             ResultPanel + UI                               */
/* -------------------------------------------------------------------------- */

type Evidence = {
  claim: string;
  evidence_url?: string; // "Resume", "JD", or http link
  justification?: string;
};

type AnalysisResult = {
  summary?: string[];
  evidence_highlights?: Evidence[];
  risk_flags?: string[];
  screening_questions?: string[];
  final_score?: number; // 0..100
};

const ScoreRing = ({ score = 0 }: { score?: number }) => {
  const value = Math.max(0, Math.min(100, score));
  const data = [
    { name: 'score', value },
    { name: 'rest', value: 100 - value },
  ];
  const ring = '#22c55e'; // emerald-500
  const track = '#e5e7eb'; // gray-200

  return (
    <div className="w-full h-48">
      <ResponsiveContainer>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            startAngle={90}
            endAngle={-270}
            innerRadius={60}
            outerRadius={80}
            stroke="none"
          >
            <Cell key="score" fill={ring} />
            <Cell key="rest" fill={track} />
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
        <div className="text-center">
          <div className="text-4xl font-extrabold">{value}</div>
          <div className="text-xs text-muted-foreground">Fit Score</div>
        </div>
      </div>
    </div>
  );
};

const CopyAllButton = ({ text }: { text: string }) => {
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={async () => {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      }}
      className="ml-auto"
    >
      {copied ? <ClipboardCheck className="w-4 h-4 mr-2" /> : <Clipboard className="w-4 h-4 mr-2" />}
      {copied ? 'Copied' : 'Copy all'}
    </Button>
  );
};

const LinkChip = ({ href }: { href?: string }) => {
  if (!href) return null;
  const isHttp = /^https?:\/\//i.test(href);
  return (
    <Badge variant="secondary" className="gap-1">
      <LinkIcon className="w-3 h-3" />
      {isHttp ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className="underline">
          Source
        </a>
      ) : (
        href
      )}
    </Badge>
  );
};

const Overview = ({ result }: { result: AnalysisResult }) => {
  const {
    final_score = 0,
    summary = [],
    evidence_highlights = [],
    risk_flags = [],
    screening_questions = [],
  } = result || {};

  return (
    <div className="space-y-6">
      {/* Score + Summary */}
      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Final Score</CardTitle>
          </CardHeader>
          <CardContent className="relative">
            <ScoreRing score={final_score} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {summary.length === 0 ? (
              <p className="text-sm text-muted-foreground">No summary available.</p>
            ) : (
              <ul className="space-y-2">
                {summary.map((s, idx) => (
                  <li key={idx} className="flex items-start gap-2">
                    <CheckCircle2 className="mt-0.5 w-4 h-4 text-emerald-500" />
                    <span className="text-sm">{s}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Evidence */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Evidence Highlights</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {evidence_highlights.length === 0 ? (
            <p className="text-sm text-muted-foreground">No evidence found.</p>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {evidence_highlights.map((ev, i) => (
                <div key={i} className="p-4 rounded-lg border bg-card/50 backdrop-blur-sm space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <h4 className="font-medium text-sm leading-5">{ev.claim}</h4>
                    <LinkChip href={ev.evidence_url} />
                  </div>
                  {ev.justification ? (
                    <>
                      <Separator />
                      <p className="text-sm text-muted-foreground">{ev.justification}</p>
                    </>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Risks */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Risk Flags</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {risk_flags.length === 0 ? (
            <p className="text-sm text-muted-foreground">No risks flagged.</p>
          ) : (
            risk_flags.map((rf, i) => (
              <Alert key={i} variant="destructive" className="py-2">
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle className="text-sm">Risk</AlertTitle>
                <AlertDescription className="text-sm">{rf}</AlertDescription>
              </Alert>
            ))
          )}
        </CardContent>
      </Card>

      {/* Screening Questions */}
      <Card>
        <CardHeader className="pb-2 flex-row items-center">
          <CardTitle className="text-base flex items-center gap-2">
            <HelpCircle className="w-4 h-4" /> Screening Questions
          </CardTitle>
          <CopyAllButton text={screening_questions.join('\n')} />
        </CardHeader>
        <CardContent>
          {screening_questions.length === 0 ? (
            <p className="text-sm text-muted-foreground">No questions generated.</p>
          ) : (
            <ol className="list-decimal ml-5 space-y-2">
              {screening_questions.map((q, i) => (
                <li key={i} className="text-sm">{q}</li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export const ResultPanel = ({ url }: { url: string }) => {
  const { data, isLoading, isError } = useQuery<AnalysisResult, Error>({
    queryKey: ['finalResult', url],
    queryFn: async () => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    staleTime: 5 * 60 * 1000,
    retry: 2,
    enabled: !!url,
  });

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (isError) {
    return (
      <Alert>
        <AlertTitle>Failed to load final results</AlertTitle>
        <AlertDescription>Check the S3 object or CORS settings.</AlertDescription>
      </Alert>
    );
  }

  return (
    <Tabs defaultValue="overview" className="w-full">
      <TabsList className="grid w-full grid-cols-2">
        <TabsTrigger value="overview">Overview</TabsTrigger>
        <TabsTrigger value="json">Raw JSON</TabsTrigger>
      </TabsList>
      <TabsContent value="overview" className="pt-4">
        <Overview result={data ?? {}} />
      </TabsContent>
      <TabsContent value="json" className="pt-4">
        <Alert>
          <AlertTitle>Raw Output</AlertTitle>
          <AlertDescription>
            <pre className="mt-2 w-full rounded-md bg-slate-950 p-4 overflow-auto">
              <code className="text-white">{JSON.stringify(data, null, 2)}</code>
            </pre>
          </AlertDescription>
        </Alert>
      </TabsContent>
    </Tabs>
  );
};

/* -------------------------------------------------------------------------- */
/*                               Page Component                               */
/* -------------------------------------------------------------------------- */

type BriefStatus = 'PENDING' | 'DONE' | 'ERROR' | string;

const getStatusVariant = (status: BriefStatus) =>
  status === 'DONE' ? 'default' : status === 'ERROR' ? 'destructive' : 'secondary';

const getStatusDescription = (status: BriefStatus) => {
  switch (status) {
    case 'PENDING':
      return 'Your brief is being processed. This usually takes a few minutes.';
    case 'DONE':
      return 'Your brief has been completed and is ready for download.';
    case 'ERROR':
      return 'There was an error processing your brief. Please try again or contact support.';
    default:
      return 'Status unknown.';
  }
};

export default function BriefDetail() {
  const { id } = useParams<{ id: string }>();
  const { toast } = useToast();

  const {
    data: brief,
    isLoading,
    isError,
    error,
  } = useQuery<BriefDetail, Error>({
    queryKey: ['brief', id],
    queryFn: () => getBrief(id!),
    enabled: !!id,
    // v5: callback receives the Query instance, not the data
    refetchInterval: (query) => {
      const status = (query.state.data as BriefDetail | undefined)?.status;
      return status === 'DONE' || status === 'ERROR' ? false : 5000;
    },
  });

  // v5 side-effect on error
  useEffect(() => {
    if (isError && error) {
      toast({
        title: 'Failed to load brief',
        description: error.message ?? 'An unexpected error occurred.',
        variant: 'destructive',
      });
    }
  }, [isError, error, toast]);

  // Loading skeletons
  if (isLoading) {
    return (
      <div className="container max-w-4xl mx-auto py-8">
        <div className="flex items-center gap-4 mb-8">
          <Skeleton className="h-10 w-10" />
          <div>
            <Skeleton className="h-8 w-48 mb-2" />
            <Skeleton className="h-4 w-32" />
          </div>
        </div>

        <div className="grid gap-6 md:grid-cols-2">
          <Card>
            <CardHeader>
              <Skeleton className="h-6 w-32" />
            </CardHeader>
            <CardContent className="space-y-4">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <Skeleton className="h-6 w-32" />
            </CardHeader>
            <CardContent className="space-y-4">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-2/3" />
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  // Error / not found
  if (isError || !brief) {
    return (
      <div className="container max-w-4xl mx-auto py-8">
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <h3 className="text-lg font-semibold mb-2">Brief not found</h3>
            <p className="text-muted-foreground mb-4">
              The brief you're looking for doesn't exist or you don't have access to it.
            </p>
            <Button asChild>
              <Link to="/briefs">Back to Briefs</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="container max-w-4xl mx-auto py-8">
      {/* Header with Back button */}
      <div className="flex items-center gap-4 mb-8">
        <Button variant="outline" size="sm" asChild>
          <Link to="/briefs">
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back
          </Link>
        </Button>
        <div>
          <h1 className="text-3xl font-bold">{brief.candidate.name}</h1>
          <p className="text-muted-foreground">{brief.job.title}</p>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Status Card */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              Status
              {brief.status === 'PENDING' && (
                <RefreshCw className="w-4 h-4 animate-spin text-muted-foreground" />
              )}
            </CardTitle>
            <CardDescription>{getStatusDescription(brief.status)}</CardDescription>
          </CardHeader>
          <CardContent>
            <Badge variant={getStatusVariant(brief.status)} className="text-sm">
              {brief.status}
            </Badge>

            {brief.status === 'PENDING' && (
              <div className="mt-4 p-4 bg-muted rounded-lg">
                <p className="text-sm text-muted-foreground">
                  <RefreshCw className="w-4 h-4 inline mr-2" />
                  Checking for updates every 5 seconds...
                </p>
              </div>
            )}

            {brief.status === 'ERROR' && (
              <div className="mt-4">
                <Alert>
                  <Terminal className="h-4 w-4" />
                  <AlertTitle>Processing failed</AlertTitle>
                  <AlertDescription>
                    Please retry from the briefs list or contact support if the problem persists.
                  </AlertDescription>
                </Alert>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Details Card */}
        <Card>
          <CardHeader>
            <CardTitle>Brief Details</CardTitle>
            <CardDescription>Information about this brief</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <p className="text-sm font-medium">Brief ID</p>
              <p className="text-sm text-muted-foreground font-mono">{brief.briefId}</p>
            </div>
            <div>
              <p className="text-sm font-medium">Candidate</p>
              <p className="text-sm text-muted-foreground">{brief.candidate.name}</p>
            </div>
            <div>
              <p className="text-sm font-medium">Job Title</p>
              <p className="text-sm text-muted-foreground">{brief.job.title}</p>
            </div>
          </CardContent>
        </Card>

        {/* Results Card — DONE only (download + visual panel) */}
        {brief.status === 'DONE' && (
          <Card className="md:col-span-2">
            <CardHeader>
              <CardTitle>Results</CardTitle>
              <CardDescription>Your brief analysis is complete</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {brief.final?.url ? (
                <>
                  <div className="flex items-center justify-between p-4 bg-muted rounded-lg">
                    <div>
                      <p className="font-medium">Final Analysis JSON</p>
                      <p className="text-sm text-muted-foreground">
                        Download the complete analysis results
                      </p>
                    </div>
                    <Button asChild>
                      <a href={brief.final.url} target="_blank" rel="noopener noreferrer">
                        <Download className="w-4 h-4 mr-2" />
                        Download JSON
                      </a>
                    </Button>
                  </div>

                  {/* New visualized panel */}
                  <ResultPanel url={brief.final.url} />
                </>
              ) : (
                <Alert>
                  <Terminal className="h-4 w-4" />
                  <AlertTitle>No result URL found</AlertTitle>
                  <AlertDescription>
                    The brief is marked as complete, but the final results are unavailable.
                  </AlertDescription>
                </Alert>
              )}
            </CardContent>
          </Card>
        )}

        {/* Analytics Placeholder */}
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>Analytics</CardTitle>
            <CardDescription>Brief performance metrics (coming soon)</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="p-4 bg-muted rounded-lg text-center">
                <p className="text-2xl font-bold">-</p>
                <p className="text-sm text-muted-foreground">Views</p>
              </div>
              <div className="p-4 bg-muted rounded-lg text-center">
                <p className="text-2xl font-bold">-</p>
                <p className="text-sm text-muted-foreground">Dwell Time</p>
              </div>
              <div className="p-4 bg-muted rounded-lg text-center">
                <p className="text-2xl font-bold">-</p>
                <p className="text-sm text-muted-foreground">Verification %</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
