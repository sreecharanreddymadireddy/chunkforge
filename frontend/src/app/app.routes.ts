import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./components/search/search').then((m) => m.SearchComponent),
  },
  {
    path: 'compare',
    loadComponent: () => import('./components/compare/compare').then((m) => m.CompareComponent),
  },
  { path: '**', redirectTo: '' },
];
