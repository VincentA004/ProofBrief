import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useToast } from '@/hooks/use-toast';
import { Plus, RefreshCw } from 'lucide-react';
import { listBriefs } from '@/api';
import type { BriefListItem } from '@/types';

export default function BriefsList() {
  const { toast } = useToast();
  const [briefs, setBriefs] = useState<BriefListItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const fetchBriefs = async (showRefreshing = false) => {
    try {
      if (showRefreshing) setIsRefreshing(true);
      const data = await listBriefs();
      setBriefs(data);
    } catch (error) {
      console.error('Failed to fetch briefs:', error);
      toast({
        title: "Failed to load briefs",
        description: error instanceof Error ? error.message : "An unexpected error occurred.",
        variant: "destructive"
      });
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    fetchBriefs();
  }, []);

  const formatDate = (dateString?: string | null) => {
    if (!dateString) return 'Unknown';
    return new Date(dateString).toLocaleDateString();
  };

  const getStatusVariant = (status: string) => {
    return status === 'DONE' ? 'default' : 'secondary';
  };

  if (isLoading) {
    return (
      <div className="container mx-auto py-8 animate-fade-in">
        <div className="flex justify-between items-center mb-8">
          <div>
            <Skeleton className="h-10 w-40 mb-2" />
            <Skeleton className="h-5 w-72" />
          </div>
          <Skeleton className="h-10 w-36" />
        </div>
        <Card className="shadow-modern backdrop-blur-sm bg-card/50">
          <CardContent className="p-6">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex justify-between items-center py-6 border-b last:border-b-0">
                <div className="space-y-3">
                  <Skeleton className="h-5 w-52" />
                  <Skeleton className="h-4 w-36" />
                </div>
                <div className="space-y-2">
                  <Skeleton className="h-6 w-20" />
                  <Skeleton className="h-4 w-24" />
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="container mx-auto py-8 animate-fade-in">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-4xl font-bold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
            Briefs
          </h1>
          <p className="text-muted-foreground mt-2">
            Manage and view your candidate briefs
          </p>
        </div>
        <div className="flex gap-3">
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchBriefs(true)}
            disabled={isRefreshing}
            className="shadow-modern hover:shadow-modern-xl transition-all duration-300"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button asChild className="gradient-primary hover:scale-105 transition-all duration-200 shadow-modern">
            <Link to="/new">
              <Plus className="w-4 h-4 mr-2" />
              New Brief
            </Link>
          </Button>
        </div>
      </div>

      {briefs.length === 0 ? (
        <Card className="shadow-modern-xl backdrop-blur-sm bg-gradient-subtle">
          <CardContent className="flex flex-col items-center justify-center py-16">
            <div className="text-center space-y-6 animate-scale-in">
              <div className="w-16 h-16 gradient-primary rounded-full flex items-center justify-center mx-auto">
                <Plus className="w-8 h-8 text-white" />
              </div>
              <div>
                <h3 className="text-xl font-semibold mb-2">No briefs yet</h3>
                <p className="text-muted-foreground max-w-md">
                  Create your first brief to get started with candidate analysis
                </p>
              </div>
              <Button asChild className="gradient-primary hover:scale-105 transition-all duration-200 shadow-modern">
                <Link to="/new">
                  <Plus className="w-4 h-4 mr-2" />
                  Create Brief
                </Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="shadow-modern-xl backdrop-blur-sm bg-card/80">
          <CardHeader className="gradient-subtle">
            <CardTitle className="text-2xl">All Briefs ({briefs.length})</CardTitle>
            <CardDescription>
              Click on any brief to view details and progress
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {/* Desktop Table */}
            <div className="hidden md:block p-6">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Candidate</TableHead>
                    <TableHead>Job Title</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Created</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {briefs.map((brief) => (
                    <TableRow key={brief.briefId} className="cursor-pointer hover:bg-muted/30 transition-colors duration-200 group">
                      <TableCell>
                        <Link 
                          to={`/briefs/${brief.briefId}`}
                          className="font-medium hover:text-primary transition-colors duration-200 group-hover:underline"
                        >
                          {brief.candidate.name}
                        </Link>
                      </TableCell>
                      <TableCell>{brief.job.title}</TableCell>
                      <TableCell>
                        <Badge variant={getStatusVariant(brief.status)}>
                          {brief.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatDate(brief.createdAt)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* Mobile Cards */}
            <div className="md:hidden space-y-4 p-6">
              {briefs.map((brief) => (
                <Link key={brief.briefId} to={`/briefs/${brief.briefId}`}>
                  <Card className="hover:shadow-modern transition-all duration-300 hover:scale-[1.02] bg-card/50 backdrop-blur-sm border-primary/10 hover:border-primary/30">
                    <CardContent className="p-5">
                      <div className="flex justify-between items-start mb-2">
                        <h3 className="font-medium">{brief.candidate.name}</h3>
                        <Badge variant={getStatusVariant(brief.status)}>
                          {brief.status}
                        </Badge>
                      </div>
                      <p className="text-sm text-muted-foreground mb-2">
                        {brief.job.title}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Created {formatDate(brief.createdAt)}
                      </p>
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}