import { useState } from 'react';
import { useForm, SubmitHandler } from 'react-hook-form';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { useToast } from '@/hooks/use-toast';
import { createBrief, startBrief } from '@/api';
import { uploadFile } from '@/uploads';

type FormInputs = {
  candidateName: string;
  jobTitle: string;
  resume: FileList;
  jobDescription: string;
};

const UPLOAD_STEPS = [
  'Creating brief...',
  'Uploading resume...',
  'Uploading job description...',
  'Starting pipeline...'
];

export default function NewBrief() {
  const navigate = useNavigate();
  const { toast } = useToast();

  const { register, handleSubmit, formState: { errors }, watch, reset } = useForm<FormInputs>({
    defaultValues: { candidateName: '', jobTitle: '', jobDescription: '' }
  });

  const [isWorking, setIsWorking] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const selectedResume = watch('resume')?.[0];

  const onSubmit: SubmitHandler<FormInputs> = async (data) => {
    const resumeFile = data.resume?.[0];

    if (!resumeFile) {
      toast({ title: 'Missing resume', description: 'Please upload a PDF resume.', variant: 'destructive' });
      return;
    }

    if (resumeFile.type !== 'application/pdf') {
      toast({ title: 'Invalid file type', description: 'Resume must be a PDF.', variant: 'destructive' });
      return;
    }

    if (!data.jobDescription?.trim()) {
      toast({ title: 'Missing job description', description: 'Please paste the job description.', variant: 'destructive' });
      return;
    }

    setIsWorking(true);
    setCurrentStep(0);

    try {
      // 1) Create brief
      const { briefId, uploads } = await createBrief(data.candidateName, data.jobTitle);
      setCurrentStep(1);

      // 2) Upload resume (PDF)
      await uploadFile(uploads.resume.putUrl, resumeFile);
      setCurrentStep(2);

      // 3) Upload job description (convert textarea text -> .txt file)
      const jdFile = new File([data.jobDescription], 'job_description.txt', { type: 'text/plain' });
      await uploadFile(uploads.jd.putUrl, jdFile);
      setCurrentStep(3);

      // 4) Start pipeline
      await startBrief(briefId);

      toast({
        title: 'Brief created successfully',
        description: 'Your brief is now being processed.'
      });

      reset();
      navigate(`/briefs/${briefId}`);
    } catch (error) {
      console.error('Failed to create brief:', error);
      toast({
        title: 'Failed to create brief',
        description: error instanceof Error ? error.message : 'An unexpected error occurred.',
        variant: 'destructive'
      });
    } finally {
      setIsWorking(false);
      setCurrentStep(0);
    }
  };

  const progress = isWorking ? ((currentStep + 1) / UPLOAD_STEPS.length) * 100 : 0;

  return (
    <div className="container max-w-3xl mx-auto py-8 animate-fade-in">
      <Card className="shadow-modern-xl backdrop-blur-sm bg-card/80">
        <CardHeader className="gradient-subtle">
          <CardTitle className="text-2xl bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
            Create New Brief
          </CardTitle>
          <CardDescription className="text-base">
            Upload a resume and paste the job description to generate a comprehensive brief.
          </CardDescription>
        </CardHeader>

        <CardContent className="p-8">
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-8">
            <div className="grid gap-6 md:grid-cols-2">
              <div className="space-y-3">
                <Label htmlFor="candidateName" className="text-sm font-medium">Candidate Name *</Label>
                <Input
                  id="candidateName"
                  disabled={isWorking}
                  className="h-12 bg-background/50 backdrop-blur-sm border-primary/20 focus:border-primary transition-all duration-200"
                  placeholder="Enter candidate's full name"
                  {...register('candidateName', { required: 'Candidate name is required' })}
                />
                {errors.candidateName && (
                  <p className="text-sm text-red-500">{errors.candidateName.message}</p>
                )}
              </div>

              <div className="space-y-3">
                <Label htmlFor="jobTitle" className="text-sm font-medium">Job Title *</Label>
                <Input
                  id="jobTitle"
                  disabled={isWorking}
                  className="h-12 bg-background/50 backdrop-blur-sm border-primary/20 focus:border-primary transition-all duration-200"
                  placeholder="Enter job title"
                  {...register('jobTitle', { required: 'Job title is required' })}
                />
                {errors.jobTitle && (
                  <p className="text-sm text-red-500">{errors.jobTitle.message}</p>
                )}
              </div>
            </div>

            <div className="space-y-3">
              <Label htmlFor="resume" className="text-sm font-medium">Resume (PDF) *</Label>
              <Input
                id="resume"
                type="file"
                accept=".pdf,application/pdf"
                disabled={isWorking}
                className="h-12 bg-background/50 backdrop-blur-sm border-primary/20 focus:border-primary transition-all duration-200 file:bg-primary file:text-primary-foreground file:border-0 file:rounded-md file:px-3 file:py-1 file:mr-3"
                {...register('resume', { required: 'Resume is required' })}
              />
              {selectedResume && (
                <div className="flex items-center space-x-2 p-3 bg-primary/5 rounded-lg border border-primary/20">
                  <div className="w-2 h-2 bg-green-400 rounded-full"></div>
                  <p className="text-sm text-muted-foreground">Selected: {selectedResume.name}</p>
                </div>
              )}
              {errors.resume && <p className="text-sm text-red-500">{errors.resume.message}</p>}
            </div>

            <div className="space-y-3">
              <Label htmlFor="jobDescription" className="text-sm font-medium">Job Description (paste) *</Label>
              <Textarea
                id="jobDescription"
                rows={10}
                disabled={isWorking}
                className="min-h-[200px] bg-background/50 backdrop-blur-sm border-primary/20 focus:border-primary transition-all duration-200 resize-none"
                placeholder="Paste the job description here..."
                {...register('jobDescription', { required: 'Job description is required' })}
              />
              {errors.jobDescription && (
                <p className="text-sm text-red-500">{errors.jobDescription.message}</p>
              )}
            </div>

            {isWorking && (
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
                disabled={isWorking}
                className="flex-1 h-12 shadow-modern hover:shadow-modern-xl transition-all duration-300"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={isWorking}
                className="flex-2 h-12 gradient-primary hover:scale-105 transition-all duration-200 shadow-modern disabled:opacity-50 disabled:scale-100"
              >
                {isWorking ? (
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
