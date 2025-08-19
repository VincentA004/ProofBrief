import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/hooks/use-toast';
import { ArrowLeft, Download, RefreshCw } from 'lucide-react';
import { getBrief } from '@/api';
import type { BriefDetail } from '@/types';

export default function BriefDetail() {
  const { id } = useParams<{ id: string }>();
  const { toast } = useToast();
  const [brief, setBrief] = useState<BriefDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isPolling, setIsPolling] = useState(false);

  const fetchBrief = async () => {
    if (!id) return;
    
    try {
      const data = await getBrief(id);
      setBrief(data);
      setIsLoading(false);
      
      // Start polling if status is PENDING
      if (data.status === 'PENDING' && !isPolling) {
        setIsPolling(true);
      } else if (data.status === 'DONE') {
        setIsPolling(false);
      }
    } catch (error) {
      console.error('Failed to fetch brief:', error);
      toast({
        title: "Failed to load brief",
        description: error instanceof Error ? error.message : "An unexpected error occurred.",
        variant: "destructive"
      });
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchBrief();
  }, [id]);

  // Polling effect
  useEffect(() => {
    if (!isPolling || !brief || brief.status === 'DONE') return;

    const interval = setInterval(fetchBrief, 5000);
    return () => clearInterval(interval);
  }, [isPolling, brief?.status]);

  const getStatusVariant = (status: string) => {
    return status === 'DONE' ? 'default' : 'secondary';
  };

  const getStatusDescription = (status: string) => {
    switch (status) {
      case 'PENDING':
        return 'Your brief is being processed. This usually takes a few minutes.';
      case 'DONE':
        return 'Your brief has been completed and is ready for download.';
      default:
        return 'Status unknown';
    }
  };

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

  if (!brief) {
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
            <CardDescription>
              {getStatusDescription(brief.status)}
            </CardDescription>
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
          </CardContent>
        </Card>

        {/* Details Card */}
        <Card>
          <CardHeader>
            <CardTitle>Brief Details</CardTitle>
            <CardDescription>
              Information about this brief
            </CardDescription>
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

        {/* Results Card - Only show when DONE */}
        {brief.status === 'DONE' && brief.final && (
          <Card className="md:col-span-2">
            <CardHeader>
              <CardTitle>Results</CardTitle>
              <CardDescription>
                Your brief analysis is complete
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between p-4 bg-muted rounded-lg">
                <div>
                  <p className="font-medium">Final Analysis JSON</p>
                  <p className="text-sm text-muted-foreground">
                    Download the complete analysis results
                  </p>
                </div>
                <Button asChild>
                  <a 
                    href={brief.final.url} 
                    target="_blank" 
                    rel="noopener noreferrer"
                  >
                    <Download className="w-4 h-4 mr-2" />
                    Download JSON
                  </a>
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Metrics Placeholder */}
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>Analytics</CardTitle>
            <CardDescription>
              Brief performance metrics (coming soon)
            </CardDescription>
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