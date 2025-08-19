import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { useToast } from '@/hooks/use-toast';
import { createBrief, startBrief } from '@/api';
import { putToPresigned } from '@/uploads';

const UPLOAD_STEPS = [
  'Creating brief...',
  'Uploading resume...',
  'Uploading job description...',
  'Starting pipeline...'
];

export default function NewBrief() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [formData, setFormData] = useState({
    candidateName: '',
    jobTitle: '',
    resumeFile: null as File | null,
    jobDescription: ''
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file && file.type === 'application/pdf') {
      setFormData(prev => ({ ...prev, resumeFile: file }));
    } else if (file) {
      toast({
        title: "Invalid file type",
        description: "Please select a PDF file for the resume.",
        variant: "destructive"
      });
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!formData.candidateName || !formData.jobTitle || !formData.resumeFile || !formData.jobDescription) {
      toast({
        title: "Missing fields",
        description: "Please fill in all required fields.",
        variant: "destructive"
      });
      return;
    }

    setIsSubmitting(true);
    setCurrentStep(0);

    try {
      // Step 1: Create brief
      const { briefId, uploads } = await createBrief(formData.candidateName, formData.jobTitle);
      setCurrentStep(1);

      // Step 2: Upload resume
      await putToPresigned(uploads.resume.putUrl, formData.resumeFile, 'application/pdf');
      setCurrentStep(2);

      // Step 3: Upload job description
      const jdBlob = new Blob([formData.jobDescription], { type: 'text/plain' });
      await putToPresigned(uploads.jd.putUrl, jdBlob, 'text/plain');
      setCurrentStep(3);

      // Step 4: Start pipeline
      await startBrief(briefId);
      
      toast({
        title: "Brief created successfully",
        description: "Your brief is now being processed."
      });

      navigate(`/briefs/${briefId}`);
    } catch (error) {
      console.error('Failed to create brief:', error);
      toast({
        title: "Failed to create brief",
        description: error instanceof Error ? error.message : "An unexpected error occurred.",
        variant: "destructive"
      });
    } finally {
      setIsSubmitting(false);
      setCurrentStep(0);
    }
  };

  const progress = isSubmitting ? ((currentStep + 1) / UPLOAD_STEPS.length) * 100 : 0;

  return (
    <div className="container max-w-3xl mx-auto py-8 animate-fade-in">
      <Card className="shadow-modern-xl backdrop-blur-sm bg-card/80">
        <CardHeader className="gradient-subtle">
          <CardTitle className="text-2xl bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
            Create New Brief
          </CardTitle>
          <CardDescription className="text-base">
            Upload a resume and job description to generate a comprehensive brief.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-8">
          <form onSubmit={handleSubmit} className="space-y-8">
            <div className="grid gap-6 md:grid-cols-2">
              <div className="space-y-3">
                <Label htmlFor="candidateName" className="text-sm font-medium">Candidate Name *</Label>
                <Input
                  id="candidateName"
                  value={formData.candidateName}
                  onChange={(e) => setFormData(prev => ({ ...prev, candidateName: e.target.value }))}
                  placeholder="Enter candidate's full name"
                  disabled={isSubmitting}
                  required
                  className="h-12 bg-background/50 backdrop-blur-sm border-primary/20 focus:border-primary transition-all duration-200"
                />
              </div>

              <div className="space-y-3">
                <Label htmlFor="jobTitle" className="text-sm font-medium">Job Title *</Label>
                <Input
                  id="jobTitle"
                  value={formData.jobTitle}
                  onChange={(e) => setFormData(prev => ({ ...prev, jobTitle: e.target.value }))}
                  placeholder="Enter job title"
                  disabled={isSubmitting}
                  required
                  className="h-12 bg-background/50 backdrop-blur-sm border-primary/20 focus:border-primary transition-all duration-200"
                />
              </div>
            </div>

            <div className="space-y-3">
              <Label htmlFor="resume" className="text-sm font-medium">Resume (PDF) *</Label>
              <Input
                id="resume"
                type="file"
                accept=".pdf,application/pdf"
                onChange={handleFileChange}
                disabled={isSubmitting}
                required
                className="h-12 bg-background/50 backdrop-blur-sm border-primary/20 focus:border-primary transition-all duration-200 file:bg-primary file:text-primary-foreground file:border-0 file:rounded-md file:px-3 file:py-1 file:mr-3"
              />
              {formData.resumeFile && (
                <div className="flex items-center space-x-2 p-3 bg-primary/5 rounded-lg border border-primary/20">
                  <div className="w-2 h-2 bg-green-400 rounded-full"></div>
                  <p className="text-sm text-muted-foreground">
                    Selected: {formData.resumeFile.name}
                  </p>
                </div>
              )}
            </div>

            <div className="space-y-3">
              <Label htmlFor="jobDescription" className="text-sm font-medium">Job Description *</Label>
              <Textarea
                id="jobDescription"
                value={formData.jobDescription}
                onChange={(e) => setFormData(prev => ({ ...prev, jobDescription: e.target.value }))}
                placeholder="Paste the job description here..."
                rows={10}
                disabled={isSubmitting}
                required
                className="min-h-[200px] bg-background/50 backdrop-blur-sm border-primary/20 focus:border-primary transition-all duration-200 resize-none"
              />
            </div>

            {isSubmitting && (
              <Card className="bg-primary/5 border-primary/20">
                <CardContent className="p-6">
                  <div className="space-y-4">
                    <div className="flex justify-between items-center text-sm">
                      <span className="font-medium">{UPLOAD_STEPS[currentStep]}</span>
                      <span className="text-primary font-bold">{Math.round(progress)}%</span>
                    </div>
                    <Progress value={progress} className="h-2" />
                    <div className="flex items-center space-x-2 text-xs text-muted-foreground">
                      <div className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse"></div>
                      <span>Processing your request...</span>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            <div className="flex gap-4 pt-4">
              <Button
                type="button"
                variant="outline"
                onClick={() => navigate('/briefs')}
                disabled={isSubmitting}
                className="flex-1 h-12 shadow-modern hover:shadow-modern-xl transition-all duration-300"
              >
                Cancel
              </Button>
              <Button 
                type="submit" 
                disabled={isSubmitting} 
                className="flex-2 h-12 gradient-primary hover:scale-105 transition-all duration-200 shadow-modern disabled:opacity-50 disabled:scale-100"
              >
                {isSubmitting ? (
                  <div className="flex items-center space-x-2">
                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
                    <span>Creating Brief...</span>
                  </div>
                ) : (
                  'Create Brief'
                )}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}