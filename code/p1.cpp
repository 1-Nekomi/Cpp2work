#include<iostream>
using namespace std;
int main()
{
	cout<<"2026111308陈依琳"<<endl;
	double BMI;                   //存放BMI
	double height;               //存放身高
	double weight;               //存放体重
	cout<<"请输入身高（米）：";
	cin>>height;
	cout<<"请输入体重（公斤）：";
	cin>>weight;;
	BMI=weight/(height*height);   //计算BMI
	cout<<"此人的身体质量指数为：";
	cout<<fixed;cout.precision(2);cout<<BMI<<"kg/m^2"<<endl;
	return 0;
}